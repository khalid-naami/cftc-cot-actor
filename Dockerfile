FROM apify/actor-python:3.12-slim

# Copy requirements and install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and actor files
COPY . ./

# Run the actor
CMD ["python3", "-m", "src.main"]
