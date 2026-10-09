"""
Package Management for User Workspaces

Provides package installation and management for user workspace Python environments.
Packages are installed to the system Python site-packages.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Awaitable, Callable, Optional

import aiohttp

from shared.subprocess_env import package_tool_env

logger = logging.getLogger(__name__)


class PackageNotFoundError(Exception):
    """Package not found on PyPI"""
    pass


class PackageInfo:
    """Package information from PyPI"""

    def __init__(self, name: str, version: str, summary: str):
        self.name = name
        self.version = version
        self.summary = summary


class WorkspacePackageManager:
    """
    Manages package installations for the workspace.

    Packages are installed to the system Python site-packages and are
    immediately available to all code running in the container.
    """

    def __init__(self, workspace_path: Path):
        """
        Initialize package manager for a workspace.

        Args:
            workspace_path: Path to the workspace directory
        """
        self.workspace_path = workspace_path
        self.requirements_file = workspace_path / "requirements.txt"

    async def get_package_info(self, package_name: str) -> PackageInfo:
        """
        Get package information from PyPI.

        Args:
            package_name: Name of the package

        Returns:
            PackageInfo object with name, version, and summary

        Raises:
            PackageNotFoundError: If package is not found on PyPI
        """
        async with aiohttp.ClientSession() as session:
            url = f"https://pypi.org/pypi/{package_name}/json"
            async with session.get(url) as resp:
                if resp.status == 404:
                    raise PackageNotFoundError(
                        f"Package '{package_name}' not found on PyPI"
                    )

                data = await resp.json()
                return PackageInfo(
                    name=data["info"]["name"],
                    version=data["info"]["version"],
                    summary=data["info"]["summary"]
                )

    async def list_installed_packages(self) -> list[dict]:
        """
        List all installed packages.

        Returns:
            List of dicts with 'name' and 'version' keys
        """
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "list",
            "--format", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=package_tool_env(),
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.warning(f"Failed to list packages: {stderr.decode()}")
            return []

        return json.loads(stdout.decode())

    async def check_for_updates(self) -> list[dict]:
        """
        Check for available updates to installed packages.

        Returns:
            List of dicts with 'name', 'current_version', and 'latest_version' keys
        """
        installed = await self.list_installed_packages()
        updates = []

        for pkg in installed:
            try:
                info = await self.get_package_info(pkg["name"])
                if info.version != pkg["version"]:
                    updates.append({
                        "name": pkg["name"],
                        "current_version": pkg["version"],
                        "latest_version": info.version
                    })
            except Exception as e:
                logger.warning(f"Failed to check updates for {pkg['name']}: {e}")

        return updates

    async def install_package(
        self,
        package_name: str,
        version: Optional[str] = None,
        log_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        timeout: int = 300,  # 5 minutes
        append_to_requirements: bool = True
    ):
        """
        Install a specific package with optional version.

        Args:
            package_name: Name of the package to install
            version: Optional version specifier (e.g., "2.31.0")
            log_callback: Optional async callback for streaming log messages
            timeout: Installation timeout in seconds (default: 300)
            append_to_requirements: Whether to append to requirements.txt (default: True)

        Raises:
            PackageNotFoundError: If package is not found on PyPI
            Exception: If installation fails or times out
        """

        async def log(msg: str):
            if log_callback:
                await log_callback(msg)

        # Validate package exists on PyPI
        await log(f"Validating package '{package_name}'...")
        try:
            info = await self.get_package_info(package_name)
            await log(f"✓ Found: {info.name} - {info.summary}")
        except PackageNotFoundError as e:
            await log(f"✗ {str(e)}")
            raise

        # Build package spec
        package_spec = f"{package_name}=={version}" if version else package_name

        # Check if already installed
        installed = await self.list_installed_packages()
        existing = next((p for p in installed if p["name"].lower() == package_name.lower()), None)

        if existing:
            if version and existing["version"] == version:
                await log(f"⚠️  {package_name} {version} is already installed")
                return
            elif version:
                await log(f"Updating {package_name} from {existing['version']} to {version}...")
            else:
                await log(f"⚠️  {package_name} {existing['version']} already installed. Upgrading to latest...")
        else:
            await log(f"Installing {package_spec}...")

        # Install package directly to system site-packages
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install",
            package_spec,
            "--upgrade",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=package_tool_env(),
        )

        # Stream output
        try:
            async with asyncio.timeout(timeout):
                if process.stdout:
                    async for line in process.stdout:
                        decoded = line.decode().strip()
                        if decoded:
                            await log(decoded)

                await process.wait()
        except asyncio.TimeoutError:
            process.kill()
            await log(f"✗ Installation timed out after {timeout} seconds")
            raise Exception(f"Package installation timed out after {timeout} seconds")

        if process.returncode != 0:
            await log("✗ Installation failed")
            raise Exception("Package installation failed")

        await log(f"✓ {package_spec} installed successfully")

        # Append to requirements.txt if requested and package is new or version changed
        if append_to_requirements and (not existing or version):
            await self._append_to_requirements(package_name, version)
            await log("✓ Updated requirements.txt")

    async def install_requirements_streaming(
        self,
        requirements_file: Optional[Path] = None,
        log_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        timeout: int = 300  # 5 minutes
    ):
        """
        Install packages from requirements.txt with streaming output.

        Args:
            requirements_file: Path to requirements.txt (defaults to workspace/requirements.txt)
            log_callback: Optional async callback for streaming log messages
            timeout: Installation timeout in seconds (default: 300)

        Raises:
            FileNotFoundError: If requirements.txt doesn't exist
            Exception: If installation fails or times out
        """

        async def log(msg: str):
            if log_callback:
                await log_callback(msg)

        if requirements_file is None:
            requirements_file = self.requirements_file

        await log(f"Reading requirements from {requirements_file.name}...")

        if not requirements_file.exists():
            await log(f"✗ {requirements_file.name} not found")
            raise FileNotFoundError(f"Requirements file not found: {requirements_file}")

        # Install packages directly to system site-packages
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install",
            "-r", str(requirements_file),
            "--upgrade",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=package_tool_env(),
        )

        # Stream output line by line
        try:
            async with asyncio.timeout(timeout):
                if process.stdout:
                    async for line in process.stdout:
                        decoded = line.decode().strip()
                        if decoded:
                            await log(decoded)

                await process.wait()
        except asyncio.TimeoutError:
            process.kill()
            await log(f"✗ Installation timed out after {timeout} seconds")
            raise Exception(f"Package installation timed out after {timeout} seconds")

        if process.returncode != 0:
            await log("✗ Installation failed")
            raise Exception("Package installation failed")

        await log("✓ Packages installed successfully")

    async def _append_to_requirements(self, package_name: str, version: Optional[str] = None):
        """
        Append a package to requirements.txt if it's not already there.

        Args:
            package_name: Name of the package
            version: Optional version specifier
        """
        package_spec = f"{package_name}=={version}" if version else package_name

        # Create requirements.txt if it doesn't exist
        if not self.requirements_file.exists():
            self.requirements_file.write_text(f"{package_spec}\n")
            logger.info(f"Created requirements.txt with {package_spec}")
            return

        # Read existing requirements
        content = self.requirements_file.read_text()
        lines = content.splitlines()

        # Check if package already exists
        package_exists = False
        updated_lines = []

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith("#"):
                updated_lines.append(line)
                continue

            # Extract package name from line (handle ==, >=, etc.)
            existing_package = line_stripped.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()

            if existing_package.lower() == package_name.lower():
                # Replace existing entry
                updated_lines.append(package_spec)
                package_exists = True
                logger.info(f"Updated {package_name} in requirements.txt")
            else:
                updated_lines.append(line)

        # Append if not found
        if not package_exists:
            updated_lines.append(package_spec)
            logger.info(f"Appended {package_spec} to requirements.txt")

        # Write back
        self.requirements_file.write_text("\n".join(updated_lines) + "\n")
