"""Resolve ina-infra roots and backend scripts without depending on monorepo layout."""

from __future__ import annotations

import os
from pathlib import Path


def ina_infra_root() -> Path:
    env = os.environ.get("INA_INFRA_ROOT")
    if env:
        return Path(env).resolve()
    # .../ina-infra/backend/app/services/paths.py → parents[3] = ina-infra
    return Path(__file__).resolve().parents[3]


def backend_scripts() -> Path:
    env = os.environ.get("INA_BACKEND_SCRIPTS")
    if env:
        return Path(env).resolve()
    return ina_infra_root() / "backend" / "scripts"


def templates_dir() -> Path:
    env = os.environ.get("INA_TEMPLATES")
    if env:
        return Path(env).resolve()
    return ina_infra_root() / "templates"


def repos_dir() -> Path:
    env = os.environ.get("REPOS_DIR")
    if env:
        return Path(env).resolve()
    sibling = ina_infra_root().parent / "repos"
    if sibling.is_dir():
        return sibling.resolve()
    return (ina_infra_root() / "repos").resolve()


def repo_root() -> Path:
    """Optional parent checkout (GitOps submodules). Prefer REPOS_DIR / INA_INFRA_ROOT."""
    env = os.environ.get("REPO_ROOT")
    if env:
        return Path(env).resolve()
    parent = ina_infra_root().parent
    if (parent / "repos").is_dir() or (parent / ".gitmodules").is_file():
        return parent.resolve()
    return ina_infra_root()


def ssh_config() -> Path:
    env = os.environ.get("SSH_CFG")
    if env:
        return Path(env).resolve()
    local = backend_scripts() / "ssh_config"
    if local.is_file():
        return local
    return repo_root() / "utils" / "ssh_config" / "config"


def push_script() -> Path:
    env = os.environ.get("PUSH_SCRIPT")
    if env:
        return Path(env).resolve()
    return backend_scripts() / "push_git_repos.sh"


def render_oai_controllers_script() -> Path:
    env = os.environ.get("INA_RENDER_OAI_SCRIPT")
    if env:
        return Path(env).resolve()
    return backend_scripts() / "render_oai_controllers.sh"


def profile_patch_mysql_script() -> Path:
    env = os.environ.get("INA_MYSQL_PATCH_SCRIPT")
    if env:
        return Path(env).resolve()
    return backend_scripts() / "profile_patch_mysql.sh"


def profile_rollout_script() -> Path:
    env = os.environ.get("INA_ROLLOUT_SCRIPT")
    if env:
        return Path(env).resolve()
    return backend_scripts() / "profile_rollout.sh"


def default_db_path() -> Path:
    env = os.environ.get("INA_DB_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return ina_infra_root() / "data" / "profiles.db"


def script_env(base: dict | None = None) -> dict:
    """Env for subprocesses that run backend/scripts/*."""
    env = dict(base if base is not None else os.environ)
    env["INA_INFRA_ROOT"] = str(ina_infra_root())
    env["REPOS_DIR"] = str(repos_dir())
    env["REPO_ROOT"] = str(repo_root())
    env["SSH_CFG"] = str(ssh_config())
    return env
