# Prerequisites

| Tool | Version | Check |
|---|---|---|
| Java JDK | 21 | `java -version` |
| Node.js | 20 or newer | `node --version` |
| Python | 3.10+ (3.12 preferred) | `python3 --version` |
| Maven | 3.9+ | `mvn --version` |
| Git | any recent | `git --version` |
| VS Code | current | — |
| Docker Desktop | current | `docker --version` (Day 3–4 only) |

`make doctor` checks all of these and tells you which are missing.

### macOS
```bash
brew install openjdk@21 node python@3.12 maven git
```

### Windows
Use [winget](https://learn.microsoft.com/windows/package-manager/):
```powershell
winget install EclipseAdoptium.Temurin.21.JDK OpenJS.NodeJS.LTS Python.Python.3.12 Apache.Maven Git.Git
```
Then **open a new terminal** so the PATH changes take effect.

### Linux (Debian/Ubuntu)
```bash
sudo apt install -y openjdk-21-jdk nodejs npm python3 maven git
```

You need administrator rights to install software for the duration of the
programme. No packages are installed by the labs themselves — the Python and
Node labs use only the standard library, and the Java labs resolve their
dependencies through Maven on first build.
