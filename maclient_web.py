#!/usr/bin/python
# -*- coding: utf-8 -*-
# if 'threading' in sys.modules:
import gevent
import gevent.monkey
gevent.monkey.patch_all()
import time
import socket
from hashlib import md5
from threading import Thread
from geventwebsocket import WebSocketError
from geventwebsocket.handler import WebSocketHandler
from webob import Request
import sys
import os
import re
from datetime import datetime, timedelta, tzinfo
from recaptcha.client import captcha

import maclient_web_bot
from maclient_web_bot import WebSocketBot, mac_version, HeheError, maxconnected

reload(sys)
sys.setdefaultencoding('utf-8')

class zh_BJ(tzinfo):
    def utcoffset(self, dt):
        return timedelta(hours = 8)
    def dst(self, dt):
        return timedelta(0)

startup_time = datetime.now(zh_BJ()).strftime('%m.%d %H:%M:%S')
_index_cache = None

def _print(str):
    open('mac_web.log', 'a', False).write(datetime.now(zh_BJ()).strftime('[%m.%d %H:%M]') + str + '\n')
    print(datetime.now(zh_BJ()).strftime('[%m.%d %H:%M]') + str)

offline_bots = {}
#maxconnected move to maclient_web_bot
connected = 0

def die_callback():
    global connected
    connected-=1

def born_callback():
    global connected
    connected+=1

class cleanup(Thread):
    def __init__(self):
        Thread.__init__(self, name = 'cleanup-thread')

    def run(self):
        _print('cleanup started')
        while True:
            for i in offline_bots:
                bot = offline_bots[i]
                #if not bot.offline:
                #    continue
                #if bot.offline == True:
                if time.time() - bot.last_offline_keepalive_time > bot.keep_time:
                    _print('request shutdown offline bot %s' % bot.loginid)
                    bot.end()
            gevent.sleep(60)
            _print('cleanup-thread keep alive')


last_reload_html, last_reload_py = 0, time.time()
def request_reload_html():
    global last_reload_html
    if time.time() - last_reload_html > 600:#every 6 min
        global _index_cache
        _index_cache = open("web.htm").read().replace('[startup_time]', startup_time)\
            .replace('[version_str]', str(mac_version))
        last_reload_time = time.time()

def request_reload_py():
    global last_reload_py
    if time.time() - last_reload_py > 60:#every 1 min
        reload(maclient_web_bot)
        from maclient_web_bot import WebSocketBot, mac_version, HeheError, maxconnected
        import maclient_player
        import maclient_network
        import maclient_logging
        import maclient_smart
        import maclient_plugin
        reload(maclient_player)
        reload(maclient_network)
        reload(maclient_logging)
        reload(maclient_smart)
        reload(maclient_plugin)
        last_reload_py = time.time()

clthread = cleanup()
clthread.start()

def websocket_app(environ, start_response):
    #cleanup()
    global clthread
    if(not clthread.isAlive()):
        clthread = cleanup()
        clthread.start() 
    global connected
    global maxconnected
    request = Request(environ)
    if request.path == '/bot' and 'wsgi.websocket' in environ:
        ws = environ["wsgi.websocket"]
        login_id = request.GET['id']
        password = request.GET['password']
        #area = request.GET.get('area', None)
        offline = request.GET.get('offline', False)
        keep_time = int(request.GET.get('keep_time', 12 * 3600))
        cmd = request.GET.get('cmd', '')
        serv = request.GET.get('serv', 'cn')
        servs = ['cn', 'cn2', 'cn3', 'jp', 'kr', 'tw', 'sg']
        if serv not in servs:
            ws.send("undefine server.\n")
            return
            
        _hash = md5(login_id + password + serv).digest()

        if maxconnected <= connected:
            ws.send("server overload.\n")
            return

        request_reload_py()
        from maclient_web_bot import WebSocketBot, mac_web_version

        #if offline and login_id not in config.allow_offline:
        #offline = False
        if not offline:
            ws.send("离线模式已经禁用,系统会在你关闭浏览器后停止挂机\n")
        else:
            ws.send("离线模式已经启用,系统会代挂N小时.\n")

        ws.send("webbot created by fffonionbinuxmengskysama [version %.5f]\n\n" % mac_web_version)
        #reconnects
        if _hash in offline_bots:
            bot = offline_bots[_hash]
            if bot.request_exit:
                ws.send("当前实例正在退出，请稍后重新登录")
                return
            ws.send("websocket client reconnected!\n")
            bot.offline = offline
            bot.ws = ws
            bot.keep_time = keep_time
            bot.last_offline_keepalive_time = time.time()
            while True:
                gevent.sleep(60)
                try:
                    ws.send('')
                    #cleanup()
                except Exception as e:
                    _print('[%s]login_id=%s client keep offline = %swork\n' % (e, login_id, offline))
                    return
        #new
        bot = WebSocketBot(ws, serv, md5(login_id + password + serv).hexdigest(), die_callback, born_callback)
        bot.loginid = login_id

        if offline:
            bot.keep_time = keep_time
            bot.last_offline_keepalive_time = time.time()
            bot.offline = True
            offline_bots[_hash] = bot

        _print("conn+%s=%d %s" % (environ.get('HTTP_X_REAL_IP', environ['REMOTE_ADDR']),
                                 connected, environ.get('HTTP_USER_AGENT', '-')))

        _print("login id=%s %s" % (login_id, ('offline=%d' % keep_time) if offline else ''))

        offline_timeout_stop = False
        while True:
            try:
                bot.run(login_id, password, cmd)
            except (socket.error, WebSocketError), e:
                #import traceback; traceback.print_exc()
                if bot.offline:
                    bot.ws = None
                    _print('[%s]id = %s keep offline work\n' % (e, login_id))
                    continue
                _print('[%s]id = %s exit bot\n' % (e, login_id))
                break
            except HeheError:#hehe
                _print('id = %s force exit.' % login_id)
                offline_timeout_stop = True
                break
            except Exception, e:
                _print('id = %s except offline=%s' % (login_id, offline))
                import traceback; traceback.print_exc(limit = 2)
                if not bot.offline:
                    break
                try:
                    bot.ws.send("".join(traceback.format_exception(*sys.exc_info())))
                except WebSocketError:
                    pass
                except Exception, e:
                    _print('main loop throw a ex.!\n')
                break
        try:
            bot.end()#release filelock
        except:
            pass
        connected -= 1
        if _hash in offline_bots:
            offline_bots.pop(_hash)
            _print("[conn-1=%d]offline bot exit. login_id=%s" % (connected, login_id))
        else:
            _print("[conn-1=%d]exit. login_id=%s" % (connected, login_id))
        # try:
        #     bot.end()
        # except HeheError:
        #     pass
        #auto release del bot
    elif request.path == '/upload_cfg':
        #request.POST['file'].file.read()
        start_response("200 OK", [("Content-Type", "text/html")])
        try:
            if not captcha.submit(request.POST['recaptcha_challenge'], request.POST['recaptcha_response'], '6Lfq-PISAAAAACT8g1dBSFoo0Lkr4XV3c__ydwIm', environ.get('HTTP_X_REAL_IP', environ['REMOTE_ADDR'])).is_valid:
                return '-1'#验证码填错
            assert('_hash' in request.POST)
            assert(len(request.POST['_hash']) == 32 and len(re.findall('[abcdef\d]+', request.POST['_hash'])[0]) == 32)
        except (KeyError, AssertionError, IndexError):
            return '-2'#少参数或不合法
        inp = request.POST['file'].file
        cfg = inp.read(16384)
        if inp.read(1):
            return '-3'#太大
        _print("[upload_cfg] hash=%s" % request.POST['_hash'])
        with open(os.path.join('configs', request.POST['_hash']), 'w') as f:
            f.write(cfg)
        return '1'
    else:
        start_response("200 OK", [("Content-Type", "text/html")])
        ol = connected# + 169
        request_reload_html()
        return _index_cache.replace('[connected]', '%d/%d' % (ol, len(offline_bots))).replace('[maxconnected]', '%s' % maxconnected)

if __name__ == '__main__':
    if not os.path.exists('configs'):
        os.mkdir('configs')
    server = gevent.pywsgi.WSGIServer(("", 8000), websocket_app, handler_class=WebSocketHandler)
    server.serve_forever()
