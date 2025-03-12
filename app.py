from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import cv2
import pytesseract
import re
from PIL import Image
import numpy as np
import os
import uuid
import json
from flask_session import Session

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Configure Flask-Session
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "/tmp/flask_session"  # Directory to store session files
app.config["SESSION_COOKIE_NAME"] = "your_session_cookie_name"  # Set a session cookie name

# Ensure the session directory exists
import os
if not os.path.exists(app.config["SESSION_FILE_DIR"]):
    os.makedirs(app.config["SESSION_FILE_DIR"])

# Initialize Flask-Session
Session(app)

# Set a secret key for session management
app.secret_key = '1755df8f0627ba9ef5a125181453f910abeefcfd5dd939c41401a9ff19f9489a'  # Replace with a secure secret key

class ReceiptItem:
    def __init__(self, description, price, is_taxable, original_price=None, discount=0.0, split_members=None, price_per_person=0.0):
        self.description = description
        self.original_price = float(price) if original_price is None else float(original_price)
        self.price = float(price)
        self.is_taxable = is_taxable
        self.discount = float(discount)
        self.split_members = split_members or []
        self.price_per_person = float(price_per_person)

    def apply_discount(self, discount):
        self.discount = float(discount)
        self.price = self.original_price - self.discount

    def split_bill(self, members):
        if members > 0:
            self.price_per_person = self.price / members
        return self.price_per_person

def preprocess_image(image_path):
    img = cv2.imread(image_path)
    if img is None:
        raise Exception("Could not read the image")
    
    # Resize image if too large
    max_dimension = 1800
    height, width = img.shape[:2]
    if max(height, width) > max_dimension:
        scale = max_dimension / max(height, width)
        img = cv2.resize(img, None, fx=scale, fy=scale)
    
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY, 21, 11
    )
    denoised = cv2.fastNlMeansDenoising(thresh)
    return denoised

def extract_receipt_data(image_path):
    try:
        processed_image = preprocess_image(image_path)
        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(processed_image, config=custom_config)
        
        lines = text.split('\n')
        items = []
        current_item = None
        
        # Modified patterns for matching
        price_pattern = r'(.*?)\s+\$(\d+\.\d{2})\s*([CH])'  # More flexible price pattern
        discount_pattern = r'YOUR\s+DISCOUNT\s+-\$(\d+\.\d{2})'
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            
            # Skip header lines and summary lines
            if any(skip in line.upper() for skip in ['SUBTOTAL', 'TOTAL', 'TENDER', 'CHANGE', 'NUMBER OF ITEMS', 'SERVED BY', 'MEMBER']):
                i += 1
                continue
            
            # Skip "YOU SAVED" lines
            if "YOU SAVED" in line:
                i += 1
                continue
            
            # Handle discount lines
            if "YOUR DISCOUNT" in line:
                if current_item:
                    discount_match = re.search(r'-\$(\d+\.\d{2})', line)
                    if discount_match:
                        discount = float(discount_match.group(1))
                        current_item.apply_discount(discount)
                i += 1
                continue
            
            # Try to match price pattern
            price_match = re.search(price_pattern, line)
            if price_match:
                description = price_match.group(1).strip()
                price = price_match.group(2)
                tax_indicator = price_match.group(3)
                
                # Clean up description
                description = re.sub(r'\s+', ' ', description)
                
                if description and not description.startswith('$'):
                    current_item = ReceiptItem(
                        description=description,
                        price=price,
                        is_taxable=(tax_indicator == 'H')
                    )
                    items.append(current_item)
            
            # Handle items with weight/quantity
            elif '@' in line and '$' in line:
                weight_match = re.search(r'(.*?)\s+@\s+\$(\d+\.\d{2})\s*/\s*kg', line)
                if weight_match:
                    description = weight_match.group(1).strip()
                    # Look ahead for the total price
                    next_line = lines[i + 1] if i + 1 < len(lines) else ""
                    price_match = re.search(r'\$(\d+\.\d{2})\s*([CH])', next_line)
                    if price_match:
                        price = price_match.group(1)
                        tax_indicator = price_match.group(2)
                        current_item = ReceiptItem(
                            description=description,
                            price=price,
                            is_taxable=(tax_indicator == 'H')
                        )
                        items.append(current_item)
            
            # Handle special cases (like split price items)
            elif '$' in line:
                special_price_match = re.search(r'(.*?)\$(\d+\.\d{2})\s*([CH])', line)
                if special_price_match:
                    description = special_price_match.group(1).strip()
                    price = special_price_match.group(2)
                    tax_indicator = special_price_match.group(3)
                    
                    if description and not description.startswith('$'):
                        current_item = ReceiptItem(
                            description=description,
                            price=price,
                            is_taxable=(tax_indicator == 'H')
                        )
                        items.append(current_item)
            
            i += 1
        
        # Print captured items for debugging
        print("\nDebug - Final Items Captured:")
        print("----------------------------")
        for item in items:
            print(f"Description: {item.description}, Price: ${item.price}, Taxable: {item.is_taxable}")
        
        return items
    
    except Exception as e:
        print(f"Error processing receipt: {str(e)}")
        return None

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        if 'receipt_image' not in request.files:
            return render_template('index.html', error='No file part')
        
        file = request.files['receipt_image']
        if file.filename == '':
            return render_template('index.html', error='No selected file')
        
        if file:
            filename = str(uuid.uuid4()) + '.jpg'
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            items = extract_receipt_data(filepath)
            if items:
                session_data = {
                    'items': [item.__dict__ for item in items],
                    'original_total': sum(item.price for item in items)
                }
                session['receipt_data'] = json.dumps(session_data)
                return redirect(url_for('verify_items'))
            else:
                return render_template('index.html', error='Failed to extract receipt data')
    
    return render_template('index.html')

@app.route('/verify_items', methods=['GET', 'POST'])
def verify_items():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_item':
            description = request.form.get('description')
            price = float(request.form.get('price'))
            is_taxable = request.form.get('is_taxable') == 'on'
            new_item = ReceiptItem(description, price, is_taxable)
            session_data = json.loads(session['receipt_data'])
            session_data['items'].append(new_item.__dict__)
            session['receipt_data'] = json.dumps(session_data)
            return redirect(url_for('verify_items'))
        elif action == 'continue':
            return redirect(url_for('split_bill'))
    
    session_data = json.loads(session['receipt_data'])
    items = [ReceiptItem(**item) for item in session_data['items']]
    return render_template('verify_items.html', items=items)

@app.route('/split_bill', methods=['GET', 'POST'])
def split_bill():
    if request.method == 'POST':
        session_data = json.loads(session['receipt_data'])
        items = [ReceiptItem(**item) for item in session_data['items']]
        
        members_dict = {}
        form_data = request.form
        print("Debug - Form data:", form_data)  # Debugging
        
        for item in items:
            num_members = int(form_data.get(f'members_{item.description}', 0))
            print(f"Debug - Item: {item.description}, Number of members: {num_members}")  # Debugging
            
            if num_members > 0:
                # Get all member names for this item
                member_names = []
                base_key = f'member_{item.description}_'
                
                # Collect all member names for this item
                for key, value in form_data.items():
                    if key.startswith(base_key) and value.strip():
                        member_names.append(value.strip())
                
                print(f"Debug - Member names for {item.description}: {member_names}")  # Debugging
                
                if member_names:
                    # Calculate exact price per person
                    price_per_person = round(item.price / len(member_names), 2)
                    # Handle any remaining cents due to rounding
                    remaining_cents = round(item.price - (price_per_person * len(member_names)), 2)
                    
                    # Add item to each member's list
                    for i, member_name in enumerate(member_names):
                        if member_name not in members_dict:
                            members_dict[member_name] = []
                        
                        # Add the remaining cents to the last person's share
                        amount = price_per_person
                        if i == len(member_names) - 1:
                            amount = round(amount + remaining_cents, 2)
                            
                        members_dict[member_name].append({
                            'item': item.description,
                            'amount': amount,
                            'taxable': item.is_taxable
                        })
        
        print("Debug - members_dict:", members_dict)  # Debugging
        
        # Calculate and verify totals
        split_total = sum(sum(item['amount'] for item in items) for items in members_dict.values())
        original_total = sum(item.price for item in items)
        print(f"Debug - Original Total: {original_total}")
        print(f"Debug - Split Total: {split_total}")
        
        session_data['items'] = [item.__dict__ for item in items]
        session_data['members'] = members_dict
        session['receipt_data'] = json.dumps(session_data)
        
        return redirect(url_for('edit_prices'))
    
    session_data = json.loads(session['receipt_data'])
    items = [ReceiptItem(**item) for item in session_data['items']]
    return render_template('split_bill.html', items=items)

@app.route('/results')
def show_results():
    session_data = json.loads(session['receipt_data'])
    items = [ReceiptItem(**item) for item in session_data['items']]
    members = session_data.get('members', {})
    original_total = round(sum(item.price for item in items), 2)
    
    print("Debug - session_data:", session_data)  # Add this line for debugging
    print("Debug - members:", members)  # Add this line for debugging
    
    member_summaries = []
    total_all_members = 0.0
    total_all_members_with_tax = 0.0
    
    for member_name, member_items in members.items():
        subtotal = round(sum(item['amount'] for item in member_items), 2)
        tax = round(sum(item['amount'] * 0.13 for item in member_items if item['taxable']), 2)
        total = round(subtotal + tax, 2)
        
        total_all_members += subtotal
        total_all_members_with_tax += total
        
        member_summaries.append({
            'name': member_name,
            'split_items': member_items,
            'subtotal': subtotal,
            'tax': tax,
            'total': total
        })
    
    total_all_members = round(total_all_members, 2)
    total_all_members_with_tax = round(total_all_members_with_tax, 2)
    
    print("Debug - Original Total:", original_total)
    print("Debug - Split Total:", total_all_members)
    print("Debug - member_summaries:", member_summaries)
    
    is_verified = abs(original_total - total_all_members) < 0.01
    
    return render_template('results.html', 
                          items=items, 
                          original_total=original_total, 
                          split_total=total_all_members, 
                          is_verified=is_verified,
                          member_summaries=member_summaries,
                          total_all_members=total_all_members,
                          total_all_members_with_tax=total_all_members_with_tax)

@app.route('/edit_prices', methods=['GET', 'POST'])
def edit_prices():
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'edit_price':
            try:
                item_index = int(request.form.get('item_index'))
                new_price_str = request.form.get('new_price')
                
                if new_price_str and new_price_str.strip():  # Check if price is provided
                    new_price = float(new_price_str)
                    
                    session_data = json.loads(session['receipt_data'])
                    items = [ReceiptItem(**item) for item in session_data['items']]
                    
                    if 0 <= item_index < len(items):
                        items[item_index].price = new_price
                        items[item_index].original_price = new_price
                        items[item_index].discount = 0.0
                        
                        # Update session data
                        session_data['items'] = [item.__dict__ for item in items]
                        session_data['original_total'] = sum(item.price for item in items)
                        session['receipt_data'] = json.dumps(session_data)
                        
                        print(f"Debug - Price updated for item {item_index}: {new_price}")  # Debug line
            
            except (ValueError, IndexError) as e:
                print(f"Debug - Error updating price: {str(e)}")  # Debug line
                flash(f'Error updating price: {str(e)}')
            
            return redirect(url_for('edit_prices'))
            
        elif action == 'finish':
            return redirect(url_for('show_results'))
    
    session_data = json.loads(session['receipt_data'])
    items = [ReceiptItem(**item) for item in session_data['items']]
    return render_template('edit_prices.html', items=items)

if __name__ == '__main__':
    app.secret_key = '1755df8f0627ba9ef5a125181453f910abeefcfd5dd939c41401a9ff19f9489a'  # Replace with a secure secret key
    app.run(debug=True)
