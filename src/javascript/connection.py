import threading, subprocess, json, time, signal
import atexit, os, sys
from . import config
from .config import debug

NODE_BIN = os.environ.get('NODE_BIN') or (getattr(os.environ, "NODE_BIN") if hasattr(os.environ, "NODE_BIN") else "node")
dn = os.path.dirname(__file__)


def is_notebook():
    try:
        from IPython import get_ipython
    except Exception:
        return False
    if "COLAB_GPU" in os.environ:
        return True

    shell = get_ipython().__class__.__name__
    if shell == "ZMQInteractiveShell":
        return True # Jupyter
    elif shell == "TerminalInteractiveShell":
        return True # IPython


def supports_color():
    """
    Returns True if the running system's terminal supports color, and False
    otherwise.
    """
    plat = sys.platform
    supported_platform = plat != "Pocket PC" and (plat == "win32" or "ANSICON" in os.environ)
    # isatty is not always implemented, #6223.
    is_a_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if 'idlelib.run' in sys.modules:
        return False
    if is_notebook():
        return True
    return supported_platform and is_a_tty


class Connection:
    def __init__(self, js_process):
        self.js_process = js_process

        self.sendQ = []
        self.com_items = []

        # Special handling for IPython jupyter notebooks
        self.stdout = sys.stdout
        self.notebook = False

        # Modified stdout
        modified_stdout = (sys.stdout != sys.__stdout__) or (getattr(sys, 'ps1', sys.flags.interactive) == '>>> ')

        if is_notebook() or modified_stdout:
            self.notebook = True
            self.stdout = subprocess.PIPE

        if supports_color():
            os.environ["FORCE_COLOR"] = "1"
        else:
            os.environ["FORCE_COLOR"] = "0"

        self.proc = self.com_thread = self.stdout_thread = None

        self.com_thread = threading.Thread(target=self.com_io, args=(), daemon=True)
        self.com_thread.start()

        # Make sure our child process is killed if the parent one is exiting
        atexit.register(self.stop)

    def readComItem(self, stream):
        # Currently this uses process standard input & standard error pipes
        # to communicate with JS, but this can be turned to a socket later on
        # ^^ Looks like custom FDs don't work on Windows, so let's keep using STDIO.
        line = stream.readline()
        if not line:
            return

        if line.startswith(b"blob!"):
            _, d, blob = line.split(b"!", maxsplit=2)
            d = json.loads(d.decode("utf-8"))
            target_len = d.pop("len")
            initial_len = len(blob)
            fetch_len = (target_len - initial_len) + 1
            debug(f"[js -> py] blob r:{d['r']}: target_len {target_len}, initial_len {initial_len}, fetch_len {fetch_len}")
            if fetch_len > 0:
                blob += stream.read(fetch_len)
            assert blob.endswith(b"\n")
            d["blob"] = blob[:-1]
            assert len(d["blob"]) == target_len
            debug(f"[js -> py] blob r:{d['r']}: {d['blob'][:20]} ... (truncated)")
            return d

        line = line.decode("utf-8")
        if not line.startswith('{"r"'):
            print("[JSE]", line)
            return
        try:
            d = json.loads(line)
            debug("[js -> py]", int(time.time() * 1000), line)
            return d
        except ValueError as e:
            print("[JSE]", line)

    # Write a message to a remote socket, in this case it's standard input
    # but it could be a websocket (slower) or other generic pipe.
    def writeAll(self, objs):
        for obj in objs:
            if type(obj) == str:
                j = obj + "\n"
            else:
                j = json.dumps(obj) + "\n"
            debug("[py -> js]", int(time.time() * 1000), j)
            if not self.proc or self.proc.poll() is not None:
                self.sendQ.append(j.encode())
                continue
            try:
                self.proc.stdin.write(j.encode())
                self.proc.stdin.flush()
            except Exception:
                self.stop()
                break

    # Reads from the socket, in this case it's standard error. Returns an array
    # of parsed responses from the server.
    def readAll(self):
        capture = self.com_items
        self.com_items = []
        return capture

    def com_io(self):
        try:
            if os.name == 'nt' and 'idlelib.run' in sys.modules:
                self.proc = subprocess.Popen(
                    [NODE_BIN, self.dn + "/js/bridge.js"],
                    stdin=subprocess.PIPE,
                    stdout=self.stdout,
                    stderr=subprocess.PIPE,
                    creationflags = subprocess.CREATE_NO_WINDOW
                )
            else:
                self.proc = subprocess.Popen(
                    [NODE_BIN, dn + "/js/bridge.js"],
                    stdin=subprocess.PIPE,
                    stdout=self.stdout,
                    stderr=subprocess.PIPE
                )

        except Exception as e:
            print(
                "--====--\t--====--\n\nBridge failed to spawn JS process!\n\nDo you have Node.js 16 or newer installed? Get it at https://nodejs.org/\n\n--====--\t--====--"
            )
            self.stop()
            raise e

        for send in self.sendQ:
            self.proc.stdin.write(send)
        self.sendQ.clear()
        self.proc.stdin.flush()

        # FIXME untested
        if self.notebook:
            self.stdout_thread = threading.Thread(target=self.stdout_read, args=(), daemon=True)
            self.stdout_thread.start()

        while self.proc.poll() is None:
            item = self.readComItem(self.proc.stderr)
            if item:
                self.com_items.append(item)
                if self.js_process.event_loop != None:
                    self.js_process.event_loop.queue.put("stdin")

    # FIXME untested
    def stdout_read(self):
        while self.proc.poll() is None:
            print(self.proc.stdout.readline().decode("utf-8"))

    def stop(self):
        try:
            self.proc.terminate()
        except Exception:
            pass
        self.js_process.event_loop = None
        self.js_process.event_thread = None
        self.js_process.executor = None
        # The "root" interface to JavaScript with FFID 0
        class Null:
            def __getattr__(self, *args, **kwargs):
                raise Exception(
                    "The JavaScript process has crashed. Please restart the runtime to access JS APIs."
                )
        self.js_process.global_jsi = Null()
        # Currently this breaks GC
        self.js_process.fast_mode = False

    def is_alive(self):
        return self.proc.poll() is None
