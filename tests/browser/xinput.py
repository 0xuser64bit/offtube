"""Real keyboard and mouse input for the browser tests, sent through the X
server's XTest extension. Chromium's own shortcuts and context menus take
input only from the windowing system; Playwright's goes straight to the page.

Usage: python xinput.py STEP...   (DISPLAY names the X server)
  click:X,Y         left-click at screen coordinates X,Y
  menu:X,Y          right-click there and wait until a context menu is shown
  key:Alt+Shift+O   press a chord, in X keysym names (Up, Return, ...)
"""
import ctypes
import ctypes.util
import sys
import time

c_int, c_long, c_uint, c_ulong, c_void_p = ctypes.c_int, ctypes.c_long, ctypes.c_uint, ctypes.c_ulong, ctypes.c_void_p
MODIFIERS = {"Alt": "Alt_L", "Ctrl": "Control_L", "Shift": "Shift_L"}
IS_VIEWABLE = 2
MENU_WAIT = 10


class WindowAttributes(ctypes.Structure):
    """Xlib's XWindowAttributes."""
    _fields_ = [("x", c_int), ("y", c_int), ("width", c_int), ("height", c_int), ("border_width", c_int),
                ("depth", c_int), ("visual", c_void_p), ("root", c_ulong), ("class_", c_int),
                ("bit_gravity", c_int), ("win_gravity", c_int), ("backing_store", c_int),
                ("backing_planes", c_ulong), ("backing_pixel", c_ulong), ("save_under", c_int),
                ("colormap", c_ulong), ("map_installed", c_int), ("map_state", c_int),
                ("all_event_masks", c_long), ("your_event_mask", c_long), ("do_not_propagate_mask", c_long),
                ("override_redirect", c_int), ("screen", c_void_p)]


def library(name):
    path = ctypes.util.find_library(name)
    if not path:
        sys.exit(f"lib{name} is missing: install libx11-6 and libxtst6")
    return ctypes.CDLL(path)


x11, xtst = library("X11"), library("Xtst")
x11.XOpenDisplay.argtypes, x11.XOpenDisplay.restype = [ctypes.c_char_p], c_void_p
x11.XDefaultRootWindow.argtypes, x11.XDefaultRootWindow.restype = [c_void_p], c_ulong
x11.XStringToKeysym.argtypes, x11.XStringToKeysym.restype = [ctypes.c_char_p], c_ulong
x11.XKeysymToKeycode.argtypes, x11.XKeysymToKeycode.restype = [c_void_p, c_ulong], ctypes.c_ubyte
x11.XQueryTree.argtypes = [c_void_p, c_ulong, ctypes.POINTER(c_ulong), ctypes.POINTER(c_ulong),
                           ctypes.POINTER(ctypes.POINTER(c_ulong)), ctypes.POINTER(c_uint)]
x11.XGetWindowAttributes.argtypes = [c_void_p, c_ulong, ctypes.POINTER(WindowAttributes)]
x11.XSync.argtypes = [c_void_p, c_int]
x11.XFree.argtypes = [c_void_p]
xtst.XTestFakeKeyEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
xtst.XTestFakeButtonEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
xtst.XTestFakeMotionEvent.argtypes = [c_void_p, c_int, c_int, c_int, c_ulong]

display = x11.XOpenDisplay(None)
if not display:
    sys.exit("cannot open the X display (is DISPLAY set?)")


def sync():
    """Wait until the server has handled everything sent so far."""
    x11.XSync(display, 0)


def keycode(name):
    keysym = x11.XStringToKeysym(MODIFIERS.get(name, name.lower() if len(name) == 1 else name).encode())
    code = x11.XKeysymToKeycode(display, keysym) if keysym else 0
    if not code:
        sys.exit(f"no key for {name!r}")
    return code


def press(chord):
    codes = [keycode(name) for name in chord.split("+")]
    for code in codes:
        xtst.XTestFakeKeyEvent(display, code, True, 0)
        sync()
    for code in reversed(codes):
        xtst.XTestFakeKeyEvent(display, code, False, 0)
        sync()


def click(x, y, button=1):
    xtst.XTestFakeMotionEvent(display, -1, x, y, 0)
    sync()
    xtst.XTestFakeButtonEvent(display, button, True, 0)
    sync()
    xtst.XTestFakeButtonEvent(display, button, False, 0)
    sync()


def popups():
    """The override-redirect windows on screen: menus, not browser windows."""
    root, parent = c_ulong(), c_ulong()
    children, count = ctypes.POINTER(c_ulong)(), c_uint()
    x11.XQueryTree(display, x11.XDefaultRootWindow(display), ctypes.byref(root), ctypes.byref(parent),
                   ctypes.byref(children), ctypes.byref(count))
    shown = set()
    for i in range(count.value):
        attributes = WindowAttributes()
        if (x11.XGetWindowAttributes(display, children[i], ctypes.byref(attributes))
                and attributes.map_state == IS_VIEWABLE and attributes.override_redirect):
            shown.add(children[i])
    if children:
        x11.XFree(children)
    return shown


def open_menu(x, y):
    before = popups()
    click(x, y, button=3)
    deadline = time.monotonic() + MENU_WAIT
    while not popups() - before:
        if time.monotonic() > deadline:
            sys.exit(f"no context menu appeared within {MENU_WAIT} s")
        time.sleep(0.05)


def main(steps):
    for step in steps:
        kind, _, arg = step.partition(":")
        if kind == "key":
            press(arg)
        elif kind in ("click", "menu"):
            x, y = (int(n) for n in arg.split(","))
            (click if kind == "click" else open_menu)(x, y)
        else:
            sys.exit(f"unknown step {step!r}")


if __name__ == "__main__":
    main(sys.argv[1:])
