#!/bin/bash
# Deploys the bundled modules to Heroku.
#
# Local working tree keeps the monolithic main.py (so the IDE is happy).
# On a throwaway deploy branch we overwrite main.py with the freshly-built
# bundle, so Heroku's `worker: python main.py` runs the NEW module code.
#
# Run from the project root: bash deploy_heroku.sh

set -e  # exit on any error

echo "==> Building single-file bundle from modules..."
python3 build_heroku.py

echo "==> Creating temporary deploy branch..."
git checkout -b heroku-deploy-tmp 2>/dev/null || git checkout heroku-deploy-tmp

# Overwrite main.py with the bundle for deployment only.
cp main_heroku.py main.py

# Make the Procfile deterministic (Heroku runs main.py = the bundle).
printf 'worker: python main.py\n' > Procfile

# Ensure the job-queue extra is present so recurring transactions work.
# (The local requirements.txt keeps getting auto-reverted to the plain name.)
if ! grep -q 'python-telegram-bot\[job-queue\]' requirements.txt; then
    sed -i 's/^python-telegram-bot$/python-telegram-bot[job-queue]/' requirements.txt
fi

git add main.py requirements.txt Procfile
git commit -m "Heroku deploy: $(date '+%Y-%m-%d %H:%M')" || true

echo "==> Pushing to Heroku..."
git push heroku heroku-deploy-tmp:main --force

echo "==> Cleaning up temporary branch..."
git checkout -- main.py Procfile requirements.txt 2>/dev/null || true
git checkout master
git branch -D heroku-deploy-tmp

echo ""
echo "✅ Deploy complete! Check logs with: heroku logs --tail -a hector-finance-tracker"
