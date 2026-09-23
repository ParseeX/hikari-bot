"""本机 HTTP → 已授权 OPPO → 集换社会话。接口只接受搜卡名和查版本价。"""
import hmac
import json
import logging
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import frida
import frida_tools

from phone import BASE, Phone

LIB_SHA256 = '9b9257b9d57c5ebed49b24d7802f6d15de8c2bc896d723f8c994aabad7b0b87c'


def rpc(operation):
    cancel = frida.Cancellable()
    timer = threading.Timer(25, cancel.cancel)
    timer.start()
    cancel.push_current()
    try:
        return operation()
    finally:
        cancel.pop_current()
        timer.cancel()


class Worker:
    def __init__(self):
        self.phone = Phone()
        self.directory = Path(os.environ['JHS_STATE_DIR'])
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_file = self.directory / 'phone-session.json'
        self.session = self.script = self.device = None
        self.identity = None
        self.last_failure = 0.0
        self.lock = threading.Lock()
        bridge = (Path(frida_tools.__file__).parent / 'bridges/java.js').read_text(encoding='utf-8')
        self.java = 'var Java=(function(){' + bridge + ';return bridge;})();\n'
        # 异常退出后，先清理由本服务记录且启动时钟仍一致的旧探针进程。
        if self.state_file.exists():
            self.identity = json.loads(self.state_file.read_text(encoding='utf-8'))

    def save_identity(self):
        self.state_file.write_text(json.dumps(self.identity), encoding='utf-8')

    def drop(self):
        try:
            if self.script:
                # 已排队的 Java 回调不能提前释放；只在核对后的进程退出时释放。
                self.script.eternalize()
        except Exception:
            pass
        try:
            if self.session:
                self.session.detach()
        except Exception:
            pass
        self.script = self.session = None
        if self.identity:
            self.phone.kill_owned(self.identity)
        self.identity = None
        self.state_file.unlink(missing_ok=True)

    def unlock(self):
        self.phone.adb('shell', 'input', 'keyevent', '224')
        pid = int(self.phone.adb('shell', 'pidof', 'com.android.systemui'))
        session = self.device.attach(pid)
        script = session.create_script(self.java + (BASE / 'unlock.js').read_text(encoding='utf-8'))
        try:
            script.load()
            rpc(script.exports_sync.unlock)
        finally:
            script.unload()
            session.detach()
        time.sleep(1)
        if self.phone.locked():
            raise RuntimeError('unlock not confirmed')

    def recover(self):
        if time.monotonic() - self.last_failure < 30:
            raise RuntimeError('recovery cooling down')
        was_locked = None
        started = time.monotonic()
        stage = 'connect'
        try:
            self.phone.connect()
            stage = 'drop_context'
            self.drop()
            stage = 'start_frida'
            self.phone.start_server()
            self.device = frida.get_device_manager().add_remote_device(f'127.0.0.1:{self.phone.port}')
            stage = 'keyguard'
            was_locked = self.phone.locked()
            if was_locked:
                stage = 'unlock'
                self.unlock()
            stage = 'open_miniapp'
            pid = self.phone.open_miniapp()
            stage = 'process_identity'
            self.identity = self.phone.identity(pid)
            if not self.identity:
                raise RuntimeError('process identity unavailable')
            self.save_identity()
            self.phone.unfreeze(pid)
            stage = 'attach_runtime'
            self.session = self.device.attach(pid)
            # 页面刚出现时运行库也可能尚未加载；只等待实际就绪条件。
            probe = self.session.create_script('''rpc.exports.path=()=>new Promise((resolve,reject)=>{
                const deadline=Date.now()+15000;
                function check(){
                    const module=Process.findModuleByName('libwxa-runtime-binding.so');
                    if(module){resolve(module.path);return;}
                    if(Date.now()>=deadline){reject(new Error('runtime not ready'));return;}
                    setTimeout(check,100);
                }check();
            });''')
            try:
                stage = 'runtime_version'
                probe.load()
                path = rpc(probe.exports_sync.path)
                import shlex
                if self.phone.root('sha256sum ' + shlex.quote(path)).split()[0] != LIB_SHA256:
                    raise RuntimeError('WeChat runtime version changed')
            finally:
                probe.unload()
            stage = 'business_context'
            self.script = self.session.create_script(self.java + (BASE / 'agent.js').read_text(encoding='utf-8'))
            self.script.on('message', lambda m, d: logging.warning('agent error') if m.get('type') == 'error' else None)
            self.script.load()
            ready_started = time.monotonic()
            if not rpc(self.script.exports_sync.init).get('ready'):
                raise RuntimeError('business context unavailable')
            logging.info('phone context recovered seconds=%.2f readiness_seconds=%.2f',
                         time.monotonic() - started, time.monotonic() - ready_started)
        except Exception as error:
            # 阶段名来自固定枚举，不记录异常正文或手机返回的敏感内容。
            logging.warning('phone recovery failed stage=%s error=%s', stage, type(error).__name__)
            self.last_failure = time.monotonic()
            self.drop()
            raise
        finally:
            if was_locked is not None:
                # UI 恢复只在会话丢失时运行，保留进入恢复前的锁屏状态。
                self.phone.adb('shell', 'input', 'keyevent', '3', check=False)
                if was_locked:
                    self.phone.adb('shell', 'input', 'keyevent', '223', check=False)

    def pause_background(self):
        if self.script:
            try:
                if not self.phone.mini_foreground():
                    rpc(lambda: self.script.exports_sync.lifecycle('pause'))
            except Exception:
                pass

    def warmup(self):
        # 启动时只准备一次，不定时查价或反复唤醒离线手机。
        started = time.monotonic()
        try:
            self.recover()
            logging.info('startup warmup ready seconds=%.2f', time.monotonic() - started)
        except Exception as error:
            logging.warning('startup warmup failed: %s', type(error).__name__)
        finally:
            self.pause_background()

    def query(self, template: str, payload: dict):
        code = (BASE / template).read_text(encoding='utf-8').replace('__INPUT__', json.dumps(payload, ensure_ascii=True))
        # 最多一次上下文重建，每个会话内最多两次只读请求。
        for generation in range(2):
            try:
                self.phone.connect()
                if self.script is None or self.phone.identity(self.identity['pid']) != self.identity:
                    self.recover()
                for attempt in range(2):
                    self.phone.unfreeze(self.identity['pid'])
                    rpc(lambda: self.script.exports_sync.lifecycle('resume'))
                    result = rpc(lambda: self.script.exports_sync.query(code))
                    if result.get('status') == 'done':
                        return result
                    if result.get('status') != 'timeout':
                        raise ValueError('source response unavailable')
                    logging.warning('query timeout attempt=%d stage=%s', attempt + 1, result.get('stage', 'unknown'))
                raise RuntimeError('query timeout')
            except ValueError:
                raise
            except Exception as error:
                logging.warning('phone query failed: %s', type(error).__name__)
                self.drop()
                if generation:
                    raise RuntimeError('phone unavailable') from None
            finally:
                self.pause_background()
        raise RuntimeError('phone unavailable')

    def versions(self, name_jp):
        entries, seen = [], set()
        for page in range(1, 31):
            data = self.query('search.js', {'name_jp': name_jp, 'page': page})
            for item in data['entries']:
                if item['id'] not in seen:
                    entries.append(item)
                    seen.add(item['id'])
            if page >= int(data['last_page']):
                return {'versions': entries}
        raise ValueError('too many versions')

    def prices(self, ids):
        prices = []
        for offset in range(0, len(ids), 5):
            batch = ids[offset:offset+5]
            try:
                result = self.query('prices.js', {'version_ids': batch})
                if {p['id'] for p in result['prices']} != set(batch):
                    raise ValueError('version mismatch')
                prices.extend(result['prices'])
            except Exception:
                prices.extend({'id': i, 'error': True} for i in batch)
        return {'prices': prices}

    def close(self):
        try:
            self.drop()
        finally:
            self.phone.close()

    def listings(self, version_id, pages):
        result = self.query('listings.js', {'card_version_id': version_id, 'pages': pages})
        return {'pages': result['pages']}

    def listing_details(self, version_id, seller_ids):
        result = self.query('listing-details.js', {'card_version_id': version_id, 'seller_ids': seller_ids})
        return {'sellers': result['sellers']}


def create_server(worker, token, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body):
            raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                self.respond(401, {'error': 'unauthorized'})
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('invalid body')
                self.connection.settimeout(5)
                payload = json.loads(self.rfile.read(size))
                if self.path == '/v1/versions':
                    name = payload['name_jp']
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
                        raise ValueError('invalid name')
                    operation = lambda: worker.versions(name.strip())
                elif self.path == '/v1/prices':
                    ids = payload['version_ids']
                    if not isinstance(ids, list) or not 1 <= len(ids) <= 20 or len(set(ids)) != len(ids) or not all(type(i) is int and 0 < i < 2**53 for i in ids):
                        raise ValueError('invalid IDs')
                    operation = lambda: worker.prices(ids)
                elif self.path == '/v1/listings':
                    version_id, pages = payload['card_version_id'], payload['pages']
                    if type(version_id) is not int or not 0 < version_id < 2**53:
                        raise ValueError('invalid version')
                    if not isinstance(pages, list) or not 1 <= len(pages) <= 5 or not all(type(p) is int and 1 <= p <= 100 for p in pages) or len(set(pages)) != len(pages):
                        raise ValueError('invalid pages')
                    operation = lambda: worker.listings(version_id, pages)
                elif self.path == '/v1/listing-details':
                    version_id, sellers = payload['card_version_id'], payload['seller_ids']
                    if type(version_id) is not int or not 0 < version_id < 2**53:
                        raise ValueError('invalid version')
                    if not isinstance(sellers, list) or not 1 <= len(sellers) <= 5 or not all(type(s) is int and 0 < s < 2**53 for s in sellers) or len(set(sellers)) != len(sellers):
                        raise ValueError('invalid sellers')
                    operation = lambda: worker.listing_details(version_id, sellers)
                else:
                    self.respond(404, {'error': 'not_found'})
                    return
            except (KeyError, TypeError, ValueError, TimeoutError):
                self.respond(400, {'error': 'invalid_request'})
                return
            if not worker.lock.acquire(timeout=10):
                self.respond(503, {'error': 'busy'})
                return
            try:
                self.respond(200, operation())
            except Exception as error:
                logging.warning('request failed: %s', type(error).__name__)
                self.respond(503, {'error': 'unavailable'})
            finally:
                worker.lock.release()

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    import fcntl
    directory = Path(os.environ['JHS_STATE_DIR'])
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        token = os.environ['JHS_ACCESS_TOKEN']
        if len(token) < 32:
            raise RuntimeError('bridge token too short')
        worker = Worker()
        server = create_server(worker, token, int(os.getenv('JHS_HTTP_PORT', '8791')))
        def stop(*unused):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            logging.info('bridge preparing phone context')
            worker.warmup()
            logging.info('bridge accepting requests on loopback')
            server.serve_forever()
        finally:
            server.server_close()
            worker.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    main()
