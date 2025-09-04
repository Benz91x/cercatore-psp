name: PSP finder (Subito)

on:
  schedule:
    - cron: "*/5 * * * *"   # ogni 5 minuti (UTC)
  workflow_dispatch: {}

permissions:
  contents: write   # serve per fare commit/push dello storico

concurrency:
  group: psp-finder
  cancel-in-progress: true

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0        # necessario per fare pull/push

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install playwright requests
          python -m playwright install --with-deps chromium
          sudo apt-get update
          sudo apt-get install -y xvfb

      - name: Run PSP finder (headful via Xvfb)
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          xvfb-run -a python -u bot_automatico_telegram.py

      - name: Persist history (commit & push)
        if: always()    # persiste anche se il run fallisce dopo l'invio
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git pull --rebase
          git add report_annunci_psp.txt
          git commit -m "chore(psp): update history" || echo "No changes"
          git push

      - name: Upload debug
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: debug
          path: |
            report_annunci_psp.txt
            debug_psp.png
            debug_psp.html
          if-no-files-found: ignore
