# EnvShield GIF Recording Guide

This directory contains scripts to record and convert GIFs for the EnvShield documentation.

## Quick Start

### 1. Install Dependencies

```bash
pip install asciinema agg
```

Or with Homebrew (macOS):
```bash
brew install asciinema agg
```

### 2. Record All GIFs

```bash
python3 record_gifs.py
```

Or record specific GIFs:
```bash
python3 record_gifs.py import setup      # Just import and setup
python3 record_gifs.py multi-service     # Just multi-service
```

### 3. Copy GIFs to Website

```bash
cp *.gif ../envshield_website/public/gifs/
```

## GIF Specifications

Each GIF is recorded with these specs:

| GIF | Duration | Window Size | What It Shows |
|---|---|---|---|
| `import.gif` | 6-8s | 100x28 | Reading `.env` → auto-generating schema |
| `setup.gif` | 8-10s | 100x28 | Interactive onboarding wizard |
| `generate.gif` | Already exists | 100x28 | Schema → typed config code |
| `multi-service.gif` | 10-12s | 100x32 | Managing multiple services |
| `doctor.gif` | 8-10s | 100x28 | Catching config drift |
| `scan.gif` | 8-10s | 100x28 | Secret & variable scanning |

## Manual Recording

If you prefer to record manually:

```bash
# Start recording
asciinema rec --window-size 100x28 -c './record-import.sh' import.cast

# Convert to GIF
agg import.cast import.gif --window-size 100x28
```

## Troubleshooting

**asciinema not found:**
```bash
pip install asciinema
```

**agg not found:**
```bash
pip install agg
```

**GIF looks slow/fast:**
Adjust the window size and playback speed in `agg`:
```bash
agg input.cast output.gif --speed 1.5  # Speed up 1.5x
```

**Recording captured too much/too little:**
Edit the `.sh` script to adjust what gets displayed, then re-record.

## Adding GIFs to Documentation

### In README.md:
```markdown
![Feature demo](.gif/import.gif)
```

### In Website (public/docs/features.html):
```html
<img src="/gifs/import.gif" alt="Importing existing configuration" />
```

### In Docs (any HTML page):
```html
<div class="feature-demo">
    <img src="/gifs/import.gif" alt="Importing existing configuration" />
</div>
```

## Next Steps

1. Run `python3 record_gifs.py` to record all GIFs
2. Copy GIFs to `/home/rabbil/dev/envshield_website/public/gifs/`
3. Test GIFs on the website
4. Commit changes:
   ```bash
   git add .gif/*.gif
   git commit -m "Add feature demonstration GIFs"
   ```

## Tips for Recording

- **Be slow:** Leave 1-2 second pauses between commands so people can read
- **Clear output:** Use `clear` to clean the terminal between sections
- **Show results:** Always show the final result of a command
- **Annotate:** Add comments with `echo` to explain what's happening
- **Real data:** Use realistic example data (real API keys format, etc.)

---

For questions, check the GIF_GUIDE.md in the project root.
