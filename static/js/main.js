document.addEventListener('DOMContentLoaded', () => {
    const splitForm = document.getElementById('split-form');
    if (splitForm) {
        splitForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const items = Array.from(document.querySelectorAll('#items-list li')).map(item => {
                const text = item.textContent;
                const match = text.match(/(.+) - \$([\d.]+) $$(Taxable|Non-taxable)$$/);
                if (match) {
                    return {
                        description: match[1].trim(),
                        price: parseFloat(match[2]),
                        is_taxable: match[3] === 'Taxable',
                        discount: parseFloat(text.match(/Discount: \$([\d.]+)/)?.[1] || 0)
                    };
                }
                return null;
            }).filter(item => item !== null);

            const members = {};
            document.querySelectorAll('#split-form input[name="members"]').forEach((input, index) => {
                members[items[index].description] = parseInt(input.value);
            });

            try {
                const response = await fetch('/api/split_bill', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ items, members })
                });

                const result = await response.json();
                displaySplitResult(result);
            } catch (error) {
                console.error('Error splitting bill:', error);
                document.getElementById('split-result').textContent = 'Error splitting bill. Please try again.';
            }
        });
    }
});

function displaySplitResult(result) {
    const splitResultDiv = document.getElementById('split-result');
    splitResultDiv.innerHTML = '';

    let total = 0;
    for (const [description, data] of Object.entries(result)) {
        const itemDiv = document.createElement('div');
        itemDiv.innerHTML = `
            <strong>${description}</strong><br>
            Price per person: $${data.price_per_person.toFixed(2)}<br>
            Total: $${data.total.toFixed(2)}
        `;
        splitResultDiv.appendChild(itemDiv);
        total += data.total;
    }

    const totalDiv = document.createElement('div');
    totalDiv.innerHTML = `<strong>Total:</strong> $${total.toFixed(2)}`;
    splitResultDiv.appendChild(totalDiv);
}