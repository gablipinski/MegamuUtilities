# Pointer Relocator

A small Windows desktop utility that recalculates a known module-relative address for a running process.

## Setup

```powershell
py -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run

```powershell
.\run_pointer_relocator.ps1
```

Choose the process, enter the module name, the module's previous base, and the previous address. The app resolves the module's current base and calculates:

```text
new address = current module base + (previous address - previous module base)
```

This works when the location's module-relative offset has remained stable, including ordinary ASLR relocation. A rebuilt module can move internal data, so the result is a candidate and should be verified before use.