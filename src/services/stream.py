

# The stream service is used to broadcast telemetry data from the primary replay process to any number of secondary processes/windows (e.g. for running data analysis or additional visualizations in parallel). It uses a simple TCP socket server to send telemetry frames as JSON-encoded messages. Secondary processes can connect to the stream server to receive real-time telemetry data for the current session.

import math
import socket
import json
import threading
import time
from PySide6.QtCore import QThread, Signal


def sanitize_for_json(obj):
    """Replace non-finite floats with None so packets are strict JSON.

    Real telemetry frames can carry NaN (e.g. ``rel_dist`` for laps where
    the timing feed reports no value). ``json.dumps`` would emit ``NaN``
    tokens that strict parsers reject; ``None`` keeps the schema keys
    intact while honestly marking the value unknown. Cached frames on
    disk are untouched - sanitization applies to the wire only.
    """
    if isinstance(obj, dict):
        return {key: sanitize_for_json(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json(value) for value in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj

class TelemetryStreamServer:

  # This class is going to be hosted by the race_replay window process, which is the primary consumer of telemetry data. It will broadcast the telemetry frames

  # A client that fails this many consecutive sends is assumed dead/slow
  # and disconnected so it can never stall the primary replay.
  MAX_CONSECUTIVE_SEND_FAILURES = 5

  def __init__(self, host='localhost', port=9999):
    self.host = host
    self.port = port
    self.clients = []
    self.clients_lock = threading.Lock()
    self.server_socket = None
    self.running = False
    # Consecutive per-client send failures (socket -> count). Guarded by
    # clients_lock alongside `clients`.
    self._send_failures = {}
    self.dropped_packets = 0
    self.sent_packets = 0

  @property
  def client_count(self):
    with self.clients_lock:
      return len(self.clients)

  def start(self):
    if self.running:
      return  # already serving; a second accept loop would be a leak
    self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
      self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except OSError:
      pass
    self.server_socket.bind((self.host, self.port))
    self.server_socket.listen(5)
    self.running = True
    threading.Thread(target=self.accept_clients, daemon=True).start()

  def accept_clients(self):
    while self.running:
      try:
        client_socket, addr = self.server_socket.accept()
        print(f"Client connected from {addr}")
        try:
          # Low-latency small packets: disable Nagle and never block the
          # replay thread on a slow reader.
          client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
          pass
        client_socket.setblocking(False)
        with self.clients_lock:
          self.clients.append(client_socket)
          self._send_failures[client_socket] = 0
        threading.Thread(target=self.handle_client, args=(client_socket,), daemon=True).start()
      except Exception as e:
        if self.running:
          print(f"Error accepting client: {e}")
        break

  def handle_client(self, client_socket):
    try:
      while self.running:
        time.sleep(1)  # Keep the connection alive
    except Exception as e:
      print(f"Client connection error: {e}")
    finally:
      try:
        client_socket.close()
      except OSError:
        pass
      with self.clients_lock:
        try:
          self.clients.remove(client_socket)
        except ValueError:
          pass  # Already removed by broadcast() or stop()
        self._send_failures.pop(client_socket, None)

  def _drop_client(self, client_socket):
    """Remove and close a client socket (callers hold no lock)."""
    try:
      client_socket.close()
    except OSError:
      pass
    with self.clients_lock:
      if client_socket in self.clients:
        self.clients.remove(client_socket)
      self._send_failures.pop(client_socket, None)

  def _send_nonblocking(self, client_socket, message):
    """Best-effort send of one packet; never blocks the replay thread.

    Returns True when the full packet was accepted by the OS. A partial or
    refused send counts as a failure: telemetry packets are periodic full
    snapshots, so the next tick supersedes a dropped one.
    """
    try:
      sent = client_socket.send(message)
      return sent == len(message)
    except (BlockingIOError, InterruptedError):
      return False
    except OSError:
      return None  # hard failure: disconnect

  def broadcast(self, data):
    message = json.dumps(sanitize_for_json(data)).encode('utf-8') + b'\n'

    with self.clients_lock:
      clients_copy = list(self.clients)

    if not clients_copy:
      return

    for client in clients_copy:
      outcome = self._send_nonblocking(client, message)
      if outcome is True:
        with self.clients_lock:
          # Only reset while still registered: a concurrent drop must not
          # resurrect a failure-map entry for a dead socket.
          if client in self.clients:
            self._send_failures[client] = 0
        continue
      if outcome is None:
        print("Telemetry client disconnected; removing.")
        self._drop_client(client)
        continue
      # Soft failure (slow reader): count and drop only after repeated
      # failures so a transient hiccup does not kill a healthy client.
      self.dropped_packets += 1
      with self.clients_lock:
        failures = self._send_failures.get(client, 0) + 1
        self._send_failures[client] = failures
      if failures >= self.MAX_CONSECUTIVE_SEND_FAILURES:
        print(f"Telemetry client too slow ({failures} failed sends); disconnecting.")
        self._drop_client(client)

    self.sent_packets += 1
  
  def stop(self):
    self.running = False
    if self.server_socket:
      try:
        self.server_socket.close()
      except OSError:
        pass
      self.server_socket = None
    with self.clients_lock:
      for client in list(self.clients):
        try:
          try:
            client.shutdown(socket.SHUT_RDWR)
          except OSError:
            pass
          client.close()
        except OSError:
          pass
      self.clients = []
      self._send_failures = {}

class TelemetryStreamClient(QThread):
    
  # This class is used by any secondary process/window that wants to consume the telemetry stream data. It connects to the stream server, receives data, and emits signals for the UI or other components to react to.

  data_received = Signal(dict)
  connection_status = Signal(str)
  error_occurred = Signal(str) 
  
  # Upper bound for the unparsed byte buffer. Beyond this the client is
  # hopelessly behind, so obsolete bytes are discarded (newest snapshot
  # wins) instead of growing without bound.
  MAX_BUFFER_BYTES = 256 * 1024

  def __init__(self, host='localhost', port=9999, coalesce=False):
    """Consume the telemetry stream.

    ``coalesce``: when True, only the newest complete packet per socket
    read is emitted. Use for purely visual consumers that render at a
    lower rate than the 25 FPS stream; snapshots are self-contained, so
    skipping intermediates loses no event history carried by the frames.
    Defaults to False to preserve exact per-packet delivery.
    """
    super().__init__()
    self.host = host
    self.port = port
    self.coalesce = coalesce
    self.socket = None
    self.connected = False
    self.running = False
    self.received_packets = 0
    self.coalesced_packets = 0
      
  def run(self):
    # Main thread loop - connects to server and receives data.
    self.running = True
    
    while self.running:
      try:
        self._connect_to_server()
        self._receive_data()
      except Exception as e:
        self.error_occurred.emit(f"Connection error: {str(e)}")
        if self.socket:
          self.socket.close()
        self.connected = False
        self.connection_status.emit("Disconnected")
        
        # Wait before attempting to reconnect
        self.sleep(2)
              
  def _connect_to_server(self):
    # Establish connection to the telemetry stream server.
    if self.connected:
      return
        
    self.connection_status.emit("Connecting...")
    self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.socket.settimeout(5.0)  # 5 second timeout
    
    try:
      self.socket.connect((self.host, self.port))
      self.connected = True
      self.connection_status.emit("Connected")
    except socket.timeout:
      self.error_occurred.emit(f"Connection timeout - is F1 Race Replay running?")
      raise
    except ConnectionRefusedError:
      self.error_occurred.emit(f"Connection refused - is F1 Race Replay running on {self.host}:{self.port}?")
      raise
          
  def _emit_line(self, line):
    try:
      data = json.loads(line.strip())
      self.data_received.emit(data)
      self.received_packets += 1
    except json.JSONDecodeError as e:
      self.error_occurred.emit(f"JSON decode error: {str(e)}")

  def _decode_line(self, raw):
    """Decode one complete line; returns (text, ok). A undecodable line is
    skipped without breaking the connection."""
    try:
      return raw.decode('utf-8'), True
    except UnicodeDecodeError as e:
      self.error_occurred.emit(f"Line decode error: {str(e)}")
      return "", False

  def _receive_data(self):
    # Receive and parse incoming telemetry data. The buffer stays bytes
    # until whole lines are split off, so a multibyte character divided
    # across two socket reads can never fail decoding or force a
    # needless reconnect.
    buffer = b""

    while self.running and self.connected:
      try:
        # Receive data in chunks
        chunk = self.socket.recv(65536)
        if not chunk:
          # Server closed connection
          self.connected = False
          break

        buffer += chunk

        # Bound the buffer: if we fall hopelessly behind, drop obsolete
        # bytes and resync on the newest complete packet.
        if len(buffer) > self.MAX_BUFFER_BYTES:
          last_nl = buffer.rfind(b'\n')
          if last_nl != -1:
            self.coalesced_packets += 1
            buffer = buffer[last_nl + 1:]
          else:
            buffer = b""
            continue

        if b'\n' not in buffer:
          continue
        *lines, buffer = buffer.split(b'\n')

        if self.coalesce:
          # Emit only the newest complete snapshot per read; older
          # intermediates are obsolete for visual consumers.
          self.coalesced_packets += max(0, len([ln for ln in lines if ln.strip()]) - 1)
          newest = None
          for raw in lines:
            if raw.strip():
              text, ok = self._decode_line(raw)
              if ok:
                newest = text
          if newest is not None:
            self._emit_line(newest)
          continue

        # Process complete messages (separated by newlines)
        for raw in lines:
          if raw.strip():
            text, ok = self._decode_line(raw)
            if ok:
              self._emit_line(text)

      except socket.timeout:
        continue  # Keep trying
      except Exception as e:
        if self.running:  # Only report error if we're still supposed to be running
          self.error_occurred.emit(f"Receive error: {str(e)}")
        break
              
  def stop(self):
    # Stop the client thread.
    self.running = False
    self.connected = False
    if self.socket:
      self.socket.close()
