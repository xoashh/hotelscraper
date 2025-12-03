# Use the official Apify Python Playwright image
# It includes Python 3.13, Playwright, and browsers (Chrome/Firefox/Webkit)
FROM apify/actor-python-playwright:3.13

# Copy dependencies first to leverage Docker cache
COPY requirements.txt ./

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source code
COPY . ./

# Run the actor
CMD ["python3", "-m", "src.main"]