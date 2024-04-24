from . import events, proxy, pyi
import threading, os, sys, inspect


class JSBridge:

    def __init__(self):
        self.event_loop = events.EventLoop(self)
        self.start = self.event_loop.startThread
        self.stop = self.event_loop.stopThread
        self.abort = self.event_loop.abortThread
        self.event_thread = threading.Thread(target=self.event_loop.loop, args=(), daemon=True)
        self.event_thread.start()
        self.executor = proxy.Executor(self.event_loop, self.event_thread)
        self.event_loop.pyi = pyi.PyInterface(self, self.executor)
        self.executor.bridge = self.event_loop.pyi
        self.global_jsi = proxy.Proxy(self.executor, 0)
        self.console = self.global_jsi.console
        self.globalThis = self.global_jsi.globalThis
        self.RegExp = self.global_jsi.RegExp
        self.fast_mode = False
        self.node_emitter_patches = False
        if self.global_jsi.needsNodePatches():
            self.node_emitter_patches = True

    def terminate(self):
        if self.event_loop:
            self.event_loop.stop()

    def require(self, name, version=None, depth=1):
        calling_dir = None
        if name.startswith("."):
            # Some code to extract the caller's file path, needed for relative imports
            try:
                namespace = sys._getframe(depth).f_globals
                cwd = os.getcwd()
                rel_path = namespace["__file__"]
                abs_path = os.path.join(cwd, rel_path)
                calling_dir = os.path.dirname(abs_path)
            except Exception:
                # On Notebooks, the frame info above does not exist, so assume the CWD as caller
                calling_dir = os.getcwd()

        return self.global_jsi.require(name, version, calling_dir, timeout=900)

    def eval_js(self, js):
        frame = inspect.currentframe()
        rv = None
        try:
            local_vars = {}
            for local in frame.f_back.f_locals:
                if not local.startswith("__"):
                    local_vars[local] = frame.f_back.f_locals[local]
            rv = self.global_jsi.evaluateWithContext(js, local_vars, forceRefs=True)
        finally:
            del frame
        return rv

    def AsyncTask(self, start=False):
        def decor(fn):
            fn.is_async_task = True
            t = self.event_loop.newTaskThread(fn)
            if start:
                t.start()

        return decor

    # You must use this Once decorator for an EventEmitter in Node.js, otherwise
    # you will not be able to off an emitter.
    def On(self, emitter, event):
        # print("On", emitter, event, onEvent)
        def decor(_fn):
            # Once Colab updates to Node 16, we can remove this.
            # Here we need to manually add in the `this` argument for consistency in Node versions.
            # In JS we could normally just bind `this` but there is no bind in Python.
            if self.node_emitter_patches:

                def handler(*args, **kwargs):
                    _fn(emitter, *args, **kwargs)

                fn = handler
            else:
                fn = _fn

            emitter.on(event, fn)
            # We need to do some special things here. Because each Python object
            # on the JS side is unique, EventEmitter is unable to equality check
            # when using .off. So instead we need to avoid the creation of a new
            # PyObject on the JS side. To do that, we need to persist the FFID for
            # this object. Since JS is the autoritative side, this FFID going out
            # of refrence on the JS side will cause it to be destoryed on the Python
            # side. Normally this would be an issue, however it's fine here.
            ffid = getattr(fn, "iffid")
            setattr(fn, "ffid", ffid)
            self.event_loop.callbacks[ffid] = fn
            return fn

        return decor

    # The extra logic for this once function is basically just to prevent the program
    # from exiting until the event is triggered at least once.
    def Once(self, emitter, event):
        def decor(fn):
            i = hash(fn)

            def handler(*args, **kwargs):
                if self.node_emitter_patches:
                    fn(emitter, *args, **kwargs)
                else:
                    fn(*args, **kwargs)
                del self.event_loop.callbacks[i]

            emitter.once(event, handler)
            self.event_loop.callbacks[i] = handler

        return decor

    def off(self, emitter, event, handler):
        emitter.off(event, handler)
        del self.event_loop.callbacks[getattr(handler, "ffid")]

    def once(self, emitter, event):
        val = self.global_jsi.once(emitter, event, timeout=1000)
        return val
