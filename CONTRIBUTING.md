# **Contributing to EnvShield 🛡️**

First off, thank you for considering contributing to EnvShield. It's people like you that make open-source such an amazing community. We welcome any and all contributions, from bug reports to feature requests and pull requests.

### **Table of Contents**

1. [Code of Conduct](#code-of-conduct)  
2. [How Can I Contribute?](#how-can-i-contribute)  
   * [Reporting Bugs](#reporting-bugs)  
   * [Suggesting Enhancements](#suggesting-enhancements)  
   * [Submitting a Pull Request](#submitting-a-pull-request)  
3. [Versioning & Releases](#versioning--releases)  
4. [Styleguides](#styleguides)

## **Code of Conduct**

This project and everyone participating in it is governed by the [EnvShield Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior.

## **How Can I Contribute?**

### **Reporting Bugs**

If you find a bug, please ensure the bug was not already reported by searching on GitHub under [Issues](https://github.com/rabbilyasar/envshield/issues).  
If you're unable to find an open issue addressing the problem, [open a new one](https://github.com/rabbilyasar/envshield/issues/new). Be sure to include a **title and clear description**, as much relevant information as possible, and a **code sample** or an **executable test case** demonstrating the expected behavior that is not occurring.

### **Suggesting Enhancements**

If you have an idea for a new feature or an improvement to an existing one, please open an issue with the "enhancement" label. Clearly describe the problem you're trying to solve and your proposed solution. This allows for a discussion with the maintainers before you spend time on implementation.

### **Submitting a Pull Request**

1. Fork the repo and create your branch from `main`.  
2. If you've added code that should be tested, add unit tests.  
3. Ensure the test suite passes (`pytest`).  
4. Make sure your code lints (`ruff check .` and `ruff format .`).  
5. Issue that pull request.

## **Development Setup**
To get started with the codebase, follow these steps:
1. **Clone the repository**
```
git clone https://github.com/rabbilyasar/envshield.git
cd envshield
```
2. **Create a virtual environment:**
It's highly recommended to use a virtual environment to manage dependencies.
```
python -m venv venv
source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
```
3. **Install dependencies:**
The project uses `pyproject.toml` to manage dependencies. Install the package in editable mode with all development dependencies:
```
pip install -e ".[dev]"
```


## **Versioning & Releases**

This section is primarily for project maintainers. EnvShield uses **Semantic Versioning** (`MAJOR.MINOR.PATCH`) and automates the versioning process using the `bump2version` tool. A release is always an explicit, maintainer-authorized action — never an automatic consequence of merging a PR.

Before bumping the version:

1. Ensure the working tree is clean (`bump2version` requires this).
2. Update `CHANGELOG.md` with a new dated section for the release, and commit it on its own (`docs: update changelog for vX.Y.Z`) — separately from the version bump.

Then, from the main branch, do not manually edit the version number in `pyproject.toml`. Use one of:

```bump2version patch```   (bug fixes, e.g. `4.5.0` -> `4.5.1`)
```bump2version minor```   (new backward-compatible features, e.g. `4.5.1` -> `4.6.0`)
```bump2version major```   (breaking changes)

This automatically increments the version in `pyproject.toml` and `.bumpversion.cfg`, creates a release commit, and creates the matching Git tag (e.g. `v4.6.0`).

Pushing the commit and tag (`git push && git push --tags`) triggers PyPI publishing and the GitHub Release — so review the release before pushing, don't push immediately after running `bump2version`.

## **Running Tests and Linting**

Before submitting a pull request, please ensure that your changes meet our quality standards by running our test and linting suites.
- **Run all tests with `pytest`:**
```
pytest
```
- **Check formatting and linting with `ruff`:**
```
# Check for linting errors
ruff check .

# Check for formatting issues
ruff format --check .
```
- **To automatically fix formatting issues:**
```
ruff format .
```

We look forward to your contributions!