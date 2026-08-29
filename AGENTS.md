# Instructions

- Prefer single quotes, but docstrings are sill double quotes.
- Use numpy docstring style when asked to write docstrings.
- Comments should start with a capital letter but not end with a period.
- Strictly typed, target Python 3.14.
- Follow pyright and ruff rules.
- Use `uv` for package management.
- Use `uv add` to add packages.
- Use `uv run` to run files.
- Use logging instead of prints with a `log` variable, unless the project already has a preference.
- Use `from __future__ import annotations` and `if TYPE_CHECKING:` for typing.
- Prefer asyncio and multiprocessing where possible.
- Make clean seperations of module files per functionality, making use of package folders with `__init__.py`.

## Qt for Python and QML

- Keep application logic in Python and presentation logic in QML. Use focused `QObject` bridge or model classes for the interface between them.
- Prefer registered QML types when editor completion and static QML analysis matter. Use `@QmlElement` to expose a type and `@QmlSingleton` only when singleton lifetime and global access are appropriate.
- Import modules containing decorated QML types before loading the QML engine so registration occurs at runtime.
- Use typed `@Slot` methods for operations called from QML. Declare every argument type and use `result=` for return values.
- Use `PySide6.QtCore.Property`, not Python's built-in `property`, for values exposed to QML. Mutable properties should have a notification `Signal` passed through `notify=` and should emit it only when the value actually changes.
- Keep Python annotations consistent with the Qt types declared by `Property`, `Signal`, and `Slot`. Python annotations are used by the Python type checker; Qt declarations define the API visible to QML.
- The `@Property` getter/setter form may require a narrow `# pyright: ignore[reportRedeclaration]` because the setter repeats the property name. Do not disable the diagnostic globally.
- QML modules declared from Python need `QML_IMPORT_NAME`, `QML_IMPORT_MAJOR_VERSION`, and optionally `QML_IMPORT_MINOR_VERSION`.
- Keep the Python entry point, Python files declaring registered QML types, and at least one QML source in `[tool.pyside6-project].files` so `pyside6-project build` can generate QML module metadata. Discover QML sources for linting and formatting with shell-expanded recursive globs.
- Run `uv run pyside6-project build` to regenerate `.qmltypes` and `qmldir` metadata. Rebuild after changing QML decorators, exposed classes, properties, signals, slots, Qt signatures, module names or versions, or the PySide6 environment. Implementation-only method changes and QML layout changes do not normally require regeneration.
- Run `uv run pyside6-project build` followed by `uv run pyside6-qmllint -i /qmldir src/project_name/qml/**/*.qml` to regenerate metadata and validate every QML source. If generated information is stale, use `uv run pyside6-project clean` followed by `uv run pyside6-project build`, then restart the QML language server.
- Treat `.qmltypes`, metatype JSON, generated registration sources, and generated `qmldir` files as build artifacts. Do not edit them manually.
- Use the QML language server and command-line tools from the same Qt/PySide6 environment as the application to avoid version and import-path mismatches.
- Format QML with `uv run pyside6-qmlformat --inplace <files>` and keep QML linting alongside Python type and lint checks.
