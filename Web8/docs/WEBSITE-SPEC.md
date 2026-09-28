# DoL8 Website Design Spec

## Brand
- Tool name: DoL8 (exact capitalization: D-o-L-8).
- Footer: "Built with PJY Teams".
- Tagline: "Every program leaves a delta. Measure it."
- Use a pixel-style display font for the DoL8 wordmark and all heading levels; keep body copy in a readable sans-serif.
- Set heading sizes in fixed CSS pixels with smaller fixed-pixel sizes on mobile; do not scale font size with viewport width.
- Professional developer-tool aesthetic. No emojis.

## Colors
- Page background: #0A1428.
- Cards and sections: #101E3A.
- Headings and primary accents: #FF8C00.
- Body highlights: #FFA940.
- Paragraph text: #C7D2E3.
- Borders: #1E3A5F, with orange emphasis.
- Buttons: #FF8C00 background with #0A1428 text.
- Code blocks: #060D1F with orange or light text.
- Avoid pure black, pure white backgrounds, and rainbow themes.

## Layout
- Center content in a max-width 960px container.
- Keep the page margins dark navy and blank; do not use full-width content.
- Maintain generous vertical spacing between sections.

## Navigation
1. Home
2. Docs
3. Install (hover/focus dropdown: Linux, Windows, Mac)
4. GitHub (external repository search link until the canonical repository URL is configured)

## Home
- Begin with a styled terminal-report hero visual showing an illustrative DoL8 report.
- Place all home content below the hero.
- Embed the supplied YouTube walkthrough in a responsive 16:9 "How to use DoL8" section.
- Explain RAPL and thermal measurement in plain language.
- Show key features, a sample report, why energy measurement matters, and a quick-install path.
- Clearly label example measurements as illustrative, not hardware validation.

## Documentation
- Explain RAPL counter readings and energy deltas.
- Document the architecture: rapl.py, thermal.py, poller.py, runner.py, stats.py, report.py, compare.py, watch.py, gate.py, cli.py, and doctor.py.
- Describe time, energy, power, peak power, temperature, throttling, cost, and CO2 estimates.
- Include CLI command references, JSON output structure, exit codes, statistics methodology, constraints, and honesty notes.

## Installation
- Linux bare metal only; Linux Mint or Ubuntu, Intel or AMD RAPL, Python 3.9+.
- State clearly that virtual machines and WSL2 are unsupported when RAPL is unavailable.
- Windows and Mac entries may include development/test setup, but must clearly state energy profiling is unsupported there.
- Show apt prerequisites, clone, virtual environment, editable install, doctor, udev permissions, first run, counter verification, and troubleshooting.
