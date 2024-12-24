#DEPRECATED
import ctypes; import webbrowser; import threading; import os; import sys

webbrowser.open("https://github.com/fleasion/fleasion/releases/tag/CLI")

ctypes.windll.user32.MessageBoxW(0, 
    "This version of Fleasion is now deprecated. You've been redirected to download the new one. Select 'Source code (zip)'.", 
    "Fleasion has been rewritten!", 
    0x0010 | 0x0001 | 0x00040000)

if os.name == 'nt':  
    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
