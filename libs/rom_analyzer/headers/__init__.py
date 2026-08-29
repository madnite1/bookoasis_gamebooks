# -*- coding: utf-8 -*-
from .detector import ConsoleHeaderDetector
from .nintendo import NintendoHeaderDetector
from .sega import SegaHeaderDetector
from .misc import MiscHeaderDetector

__all__ = ["ConsoleHeaderDetector", "NintendoHeaderDetector", "SegaHeaderDetector", "MiscHeaderDetector"]
