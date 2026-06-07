import ast
import os

def check_syntax(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        source = f.read()
    try:
        ast.parse(source, filename=file_path)
    except SyntaxError as e:
        print(f"Syntax Error in {file_path}: {e}")
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")

for root, _, files in os.walk('.'):
    if '.venv' in root or '__pycache__' in root or '.git' in root: continue
    for file in files:
        if file.endswith('.py'):
            check_syntax(os.path.join(root, file))
