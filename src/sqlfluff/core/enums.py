"""Enums used by sqlfluff."""
from enum import Enum

from colorama import Fore, Style


class FormatType(Enum):
    """Enums for formatting types."""

    human = "human"
    json = "json"
    yaml = "yaml"
    github_annotation = "github-annotation"
    github_annotation_native = "github-annotation-native"


class Color(Enum):
    """Colors used by `colorize` function."""

    red = Fore.RED
    green = Fore.GREEN
    blue = Fore.BLUE
    lightgrey = Fore.BLACK + Style.BRIGHT
