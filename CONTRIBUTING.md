# Contributing to 8DoTs (DoL8)

First off, thank you for considering contributing to 8DoTs!  
Making energy consumption visible in software development is a massive challenge, and we need all the help we can get. Whether you're fixing a typo, optimizing a polling thread, or adding a new feature, your help is highly valued.

##  Good First Issues
If you are new to open source or just new to this project, don't be intimidated! We actively label issues that are perfect for beginners. 
Look for issues tagged with:
- 🟢 `good first issue`
- 🟡 `help wanted`

*(Note: To **use** the tool, you need Linux with RAPL. But to **contribute code and run tests**, you do NOT need special hardware! We use "mock" fake hardware in our test suite, so you can write and test code on a Mac or Windows PC.)*

##  How to Set Up Your Local Environment

1. **Fork and Clone the Repository**
   ```bash
   git clone https://github.com/YOUR-USERNAME/8DoTs.git
   cd 8DoTs
   ```

2. **Create a Virtual Environment**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
   ```

3. **Install Dependencies (including test tools)**
   ```bash
   python -m pip install -e ".[test]"
   ```

##  Running the Tests
We rely heavily on `pytest`. Because we mock the Linux `sysfs` and `psutil` interfaces, the tests will run perfectly on any operating system.

Run the full test suite:
```bash
python -m pytest
```
*Rule of thumb: If you add a new feature or fix a bug, please add a test for it! If you are unsure how to test it, open your PR anyway and ask for help.*

##  The Contribution Workflow

1. **Find or Open an Issue:** Check the [Issues tab](https://github.com/uyg7x/8DoTs/issues). If you want to work on something that isn't listed, open a new issue first so we can discuss it.
2. **Create a Branch:** Never work directly on the `main` branch.
   ```bash
   git checkout -b fix/issue-7-overlapping-text
   ```
   *(Tip: Name your branch after the issue number, e.g., `fix/issue-7` or `feat/issue-17`)*
3. **Make Your Changes:** Write clean, well-commented code.
4. **Commit and Push:** 
   ```bash
   git add .
   git commit -m "Fix overlapping text in terminal output (Closes #7)"
   git push origin fix/issue-7-overlapping-text
   ```
5. **Open a Pull Request (PR):** Go to your fork on GitHub and click "Contribute" -> "Open Pull Request". 

