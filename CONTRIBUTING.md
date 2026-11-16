# Contributing to Akemi

Thank you for considering contributing to Akemi! Please read this document to understand how you can help.

## How to Contribute

1. Fork the repository
2. Create a new branch for your feature or bugfix
3. Make your changes
4. Install with `pip install -e ".[dev]"` and run `pytest`
5. Submit a pull request

## Code Style

We follow PEP 8 for Python code. Please use tools like `black` and `flake8` to maintain consistency.

### Linting e Formatação

Antes de enviar um PR, rode:

```bash
# Formatação automática
black akemi tests

# Verificar estilo
flake8 akemi tests

# Type checking (opcional mas recomendado)
mypy akemi
```

A configuração está em `pyproject.toml` e `.flake8`.

## Reporting Issues

Please use the GitHub issue tracker to report bugs or suggest features. When reporting a bug, include:
- Your operating system and Python version
- Steps to reproduce the issue
- Expected behavior vs actual behavior
- Any relevant logs or error messages

## License

By contributing to Akemi, you agree that your contributions will be licensed under the Apache License 2.0.