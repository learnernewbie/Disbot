import os

# Create necessary data directories if they don't exist
data_directories = [
    'data',
]

for directory in data_directories:
    if not os.path.exists(directory):
        os.makedirs(directory)
