"""
Run this to produce a single-file main_heroku.py for Heroku deployment.
Usage: python build_heroku.py
"""

SOURCES = [
    "utils/sheets.py",
    "finance/bot.py",
    "production/bot.py",
    "heroku_entry.py",
]

# Local cross-module imports to drop (they get inlined by the concatenation)
SKIP_LOCAL_IMPORTS = {
    "from utils.sheets import get_google_client",
    "from finance.bot import FinanceBot",
    "from production.bot import ProductionBot",
}


def read_import_blocks(lines):
    """
    Split a file's lines into a list of (block_text, is_import) tuples.
    Multi-line imports (open parentheses) are kept as a single block.
    """
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Start of an import statement
        if stripped.startswith("import ") or stripped.startswith("from "):
            block = [line]
            # Multi-line: collect until closing paren
            if "(" in stripped and ")" not in stripped:
                i += 1
                while i < len(lines):
                    block.append(lines[i])
                    if lines[i].strip().startswith(")"):
                        break
                    i += 1
            blocks.append(("".join(block), True))
        else:
            blocks.append((line, False))
        i += 1
    return blocks


def main():
    seen_imports = set()
    output_lines = []

    for filepath in SOURCES:
        with open(filepath, "r") as f:
            raw_lines = f.readlines()

        output_lines.append(f"\n# {'='*78}\n# SOURCE: {filepath}\n# {'='*78}\n")

        blocks = read_import_blocks(raw_lines)
        for block_text, is_import in blocks:
            if not is_import:
                output_lines.append(block_text)
                continue

            first_line = block_text.strip().splitlines()[0].strip()

            # Drop local cross-module imports
            if first_line in SKIP_LOCAL_IMPORTS:
                continue

            # De-duplicate: skip if first line already seen
            if first_line in seen_imports:
                continue

            seen_imports.add(first_line)
            output_lines.append(block_text)

    output_path = "main_heroku.py"
    with open(output_path, "w") as f:
        f.writelines(output_lines)

    code_lines = sum(1 for l in output_lines if l.strip() and not l.strip().startswith("#"))
    print(f"Built {output_path} — {code_lines} code lines.")


if __name__ == "__main__":
    main()
