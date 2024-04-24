# This file contains all the exposed modules
from . import bridge


_js_bridge = None


def init():
    global _js_bridge, console, globalThis, RegExp, start, stop, abort
    _js_bridge = bridge.JSBridge()
    console = _js_bridge.console
    globalThis = _js_bridge.globalThis
    RegExp = _js_bridge.RegExp
    start = _js_bridge.start
    stop = _js_bridge.stop
    abort = _js_bridge.abort


init()


def terminate():
    if _js_bridge:
        _js_bridge.terminate()


def require(name, version=None):
    if _js_bridge:
        return _js_bridge.require(name, version, depth=2)


def eval_js(js):
    if _js_bridge:
        return _js_bridge.eval_js(js)


def AsyncTask(start=False):
    if _js_bridge:
        return _js_bridge.AsyncTask(start)


def On(emitter, event):
    if _js_bridge:
        return _js_bridge.On(emitter, event)


def Once(emitter, event):
    if _js_bridge:
        return _js_bridge.Once(emitter, event)


def off(emitter, event, handler):
    if _js_bridge:
        return _js_bridge.off(emitter, event, handler)


def once(emitter, event):
    if _js_bridge:
        return _js_bridge.once(emitter, event)
