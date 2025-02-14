import json
import os
from typing import Dict, Any

class DatabaseManager:
    def __init__(self, data_folder: str = "data"):
        self.data_folder = data_folder
        self.ensure_data_folder()

    def ensure_data_folder(self):
        if not os.path.exists(self.data_folder):
            os.makedirs(self.data_folder)

    def load_data(self, filename: str) -> Dict[str, Any]:
        filepath = os.path.join(self.data_folder, filename)
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def save_data(self, filename: str, data: Dict[str, Any]):
        filepath = os.path.join(self.data_folder, filename)
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=4)

    def append_data(self, filename: str, key: str, value: Any):
        data = self.load_data(filename)
        if key not in data:
            data[key] = []
        data[key].append(value)
        self.save_data(filename, data)

    def remove_data(self, filename: str, key: str):
        data = self.load_data(filename)
        if key in data:
            del data[key]
            self.save_data(filename, data)
