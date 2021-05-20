import os
import subprocess

from typing import Dict, Optional

from cfengine import PromiseModule, ValidationError, Result
from pydantic import (
    BaseModel,
    ValidationError as PydanticValidationError,
    validator,
)


class GitPromiseTypeModel(BaseModel):
    destination: str
    repository: str
    bare: bool = False
    clone: bool = True
    depth: int = 0
    executable: str = "git"
    force: bool = False
    recursive: bool = True
    reference: Optional[str]
    remote: str = "origin"
    ssh_options: Optional[str]
    update: bool = True
    version: str = "HEAD"

    @validator("destination")
    def destination_must_be_absolute(cls, v):
        if not os.path.isabs(v):
            raise ValueError("must be an absolute path")
        return v

    @validator("depth")
    def depth_must_be_positive(cls, v):
        if not v >= 0:
            raise ValueError("must be a positive number")
        return v


class GitPromiseTypeModule(PromiseModule):
    def __init__(self, **kwargs):
        super(GitPromiseTypeModule, self).__init__(
            "git_promise_module", "0.1.0", **kwargs
        )

    def validate_promise(self, promiser: str, attributes: Dict):
        attributes.setdefault("destination", promiser)
        try:
            GitPromiseTypeModel(**attributes)
        except PydanticValidationError as e:
            errors = [
                ".".join(map(str, err["loc"])) + ": " + err["msg"] for err in e.errors()
            ]
            raise ValidationError(", ".join(errors))

    def evaluate_promise(self, promiser: str, attributes: Dict):
        safe_promiser = promiser.replace(",", "_")
        attributes.setdefault("destination", promiser)
        model = GitPromiseTypeModel(**attributes)

        classes = []
        result = Result.KEPT

        # if the repository doesn't exist
        if not os.path.exists(model.destination):
            if not model.clone:
                return (Result.NOT_KEPT, [f"{safe_promiser}_not_found"])
            try:
                self.log_info(
                    f"Cloning '{model.repository}:{model.version}' to '{model.destination}'"
                )
                clone_options = []
                if model.bare:
                    clone_options += ["--bare"]
                if model.depth:
                    clone_options += [f"--depth={str(model.depth)}"]
                if model.reference:
                    clone_options += ["--reference", model.reference]
                output = subprocess.check_output(
                    [
                        model.executable,
                        "clone",
                        model.repository,
                        model.destination,
                        "--origin",
                        model.remote,
                        "--branch",
                        model.version,
                    ]
                    + clone_options,
                    env=self._git_envvars(model),
                )
                classes.append(f"{safe_promiser}_cloned")
                result = Result.REPAIRED
            except subprocess.CalledProcessError as e:
                error = e.output.decode()
                self.log_error(f"Failed clone: {error}")
                return (Result.NOT_KEPT, [f"{safe_promiser}_clone_failed"])

        else:
            # discard local changes to the repository
            if model.force:
                try:
                    output = subprocess.check_output(
                        [model.executable, "status", "--porcelain"],
                        cwd=model.destination,
                        env=self._git_envvars(model),
                    )
                    if output.decode("utf-8").strip() != "":
                        self.log_info(f"Reset '{model.destination}' to HEAD")
                        output = subprocess.check_output(
                            [model.executable, "reset", "--hard", "HEAD"],
                            cwd=model.destination,
                            env=self._git_envvars(model),
                        )
                        output = subprocess.check_output(
                            [model.executable, "clean", "-f"],
                            cwd=model.destination,
                            env=self._git_envvars(model),
                        )
                        classes.append(f"{safe_promiser}_reset")
                        result = Result.REPAIRED
                except subprocess.CalledProcessError as e:
                    error = e.output.decode()
                    self.log_error(f"Failed reset: {error}")
                    return (Result.NOT_KEPT, [f"{safe_promiser}_reset_failed"])

            # Update the repository
            if model.update:
                try:
                    self.log_info(f"Updating '{model.repository}' in '{model.destination}'")
                    # fetch the remote
                    output = subprocess.check_output(
                        [model.executable, "fetch", model.remote],
                        cwd=model.destination,
                        env=self._git_envvars(model),
                    )
                    # checkout the branch, if different from the current one
                    output = subprocess.check_output(
                        [model.executable, "rev-parse", "--abbrev-ref", "HEAD"],
                        cwd=model.destination,
                        env=self._git_envvars(model),
                    )
                    detached = False
                    if output.decode("utf-8").strip() == "HEAD":
                        detached = True
                        output = subprocess.check_output(
                            [model.executable, "rev-parse", "HEAD"],
                            cwd=model.destination,
                            env=self._git_envvars(model),
                        )
                    if output.decode("utf-8").strip() != model.version:
                        output = subprocess.check_output(
                            [model.executable, "checkout", model.version],
                            cwd=model.destination,
                            env=self._git_envvars(model),
                        )
                        result = Result.REPAIRED
                    # check if merge with the remote branch is needed
                    if not detached:
                        output = subprocess.check_output(
                            [
                                model.executable,
                                "diff",
                                f"..{model.remote}/{model.version}",
                            ],
                            cwd=model.destination,
                            env=self._git_envvars(model),
                        )
                        if output.decode("utf-8") != "":
                            output = subprocess.check_output(
                                [
                                    model.executable,
                                    "merge",
                                    model.remote + "/" + model.version,
                                ],
                                cwd=model.destination,
                                env=self._git_envvars(model),
                            )
                            result = Result.REPAIRED
                    classes.append(f"{safe_promiser}_updated")
                except subprocess.CalledProcessError as e:
                    error = e.output.decode()
                    self.log_error(f"Failed fetch: {error}")
                    return (Result.NOT_KEPT, [f"{safe_promiser}_update_failed"])

        # everything okay
        return (result, classes)

    def _git_envvars(self, model: GitPromiseTypeModel):
        env = os.environ.copy()
        env["GIT_SSH_COMMAND"] = f"{model.executable}"
        if model.ssh_options:
            env["GIT_SSH_COMMAND"] += " " + model.ssh_options
        return env


if __name__ == "__main__":
    GitPromiseTypeModule().start()