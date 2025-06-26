# EnvGuard: Secure Environment Variable Management CLI

EnvGuard is a powerful Command Line Interface (CLI) tool designed to streamline local environment variable management, enforce security best practices (like preventing accidental secret commits), simplify template synchronization, and facilitate secure sharing of non-sensitive environment snippets among developers.

## Features

* **Interactive Onboarding:** Guided setup for new projects and developers.
* **Multi-Profile Management:** Define and switch between named environment profiles (dev, staging, prod).
* **Secure Template Synchronization:** Automatically generates and keeps `.env.template` files in sync, replacing sensitive values with placeholders.
* **Proactive Secret Prevention:** Integrates with Git pre-commit hooks to scan staged files for hardcoded secrets, blocking commits.
* **Basic Secure Snippet Sharing:** Encrypts and shares non-sensitive environment variables via a passphrase, ensuring local-to-local transfer.
* **Schema Validation:** Define rules for your environment variables (type, required, enum, pattern) to ensure data integrity.
* **Extensible Parsers:** Support for various environment file formats (.env, JSON, YAML).
* **Rich CLI Output:** Utilizes colors, icons, and clear formatting for an enhanced user experience.

## Installation

### Prerequisites

* Python 3.8 or higher
* `pip` (Python package installer)

### Steps

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/rabbilyasar/envguard.git](https://github.com/rabbilyasar/envguard.git) 
    cd envguard
    ```
2.  **(Recommended) Create and activate a Python virtual environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate # On Windows: .\venv\Scripts\activate
    ```
3.  **Install EnvGuard in editable mode (for development):**
    This allows you to make changes to the code and see them reflected immediately without re-installation.
    ```bash
    pip install -e .
    ```
    If you just want to use EnvGuard as a tool:
    ```bash
    pip install .
    ```

## Usage

(More detailed usage instructions and examples will be added here as features are implemented in the development process.)

To see the basic commands:
```bash
envguard --help
```
## Contributing
Contributions are welcome! Please refer to the project's guidelines for more information.

## License
This project is licensed under the MIT License - see the LICENSE file for details.