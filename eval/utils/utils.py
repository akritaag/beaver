import json

def read_json(fn):
    with open(fn) as f:
        return json.load(f)

def write_json(obj, fn):
    with open(fn, 'w') as f:
        json.dump(obj, f, indent=2)