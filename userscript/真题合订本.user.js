// ==UserScript==
// @name         真题合订本 · 一键生成
// @namespace    hebing.exam
// @version      0.2.0
// @description  在网盘页面放一个按钮：文件下载好后，点一下就在本机把题目/答案合成一本「题目→答案→解析」的合订本。需要本机先启动 hebing 服务。
// @author       you
// @match        *://pan.baidu.com/*
// @match        *://yun.baidu.com/*
// @match        *://*/*
// @connect      127.0.0.1
// @grant        GM_xmlhttpRequest
// @grant        GM_openInTab
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const HOST = 'http://127.0.0.1:8777';

  function onNetdiskPage() {
    const h = location.hostname;
    return /baidu\.com$/.test(h) || /pan\.|yun\./.test(h);
  }

  // 只在网盘页面出现，避免污染其它网站
  if (!onNetdiskPage()) return;

  const css = `
    #hb-fab {
      position: fixed; right: 22px; bottom: 96px; z-index: 2147483000;
      display: flex; align-items: center; gap: 8px;
      padding: 11px 18px; border-radius: 999px; cursor: pointer;
      background: #1f3b73; color: #fff; font: 13px/1.2 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
      box-shadow: 0 6px 20px rgba(31,59,115,.35); user-select: none;
      transition: transform .15s, box-shadow .15s;
    }
    #hb-fab:hover { transform: translateY(-1px); box-shadow: 0 8px 24px rgba(31,59,115,.45); }
    #hb-fab.busy { background: #8a94a6; pointer-events: none; }
    #hb-toast {
      position: fixed; right: 22px; bottom: 156px; z-index: 2147483000;
      max-width: 380px; padding: 12px 14px; border-radius: 10px;
      background: #1b2430; color: #fff; font: 12.5px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
      box-shadow: 0 8px 28px rgba(0,0,0,.3); white-space: pre-wrap; display: none;
    }
    #hb-toast code { background: #2f3b4d; padding: 1px 5px; border-radius: 4px; }
  `;

  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  const fab = document.createElement('div');
  fab.id = 'hb-fab';
  fab.textContent = '📚 生成真题合订本';
  document.body.appendChild(fab);

  const toast = document.createElement('div');
  toast.id = 'hb-toast';
  document.body.appendChild(toast);

  let toastTimer = null;
  function say(msg, ms = 6000) {
    toast.innerHTML = msg;
    toast.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.style.display = 'none'; }, ms);
  }

  function ping(cb) {
    GM_xmlhttpRequest({
      method: 'GET',
      url: HOST + '/api/ping',
      timeout: 4000,
      onload: r => {
        try { cb(null, JSON.parse(r.responseText)); }
        catch (e) { cb(e); }
      },
      onerror: () => cb(new Error('no service')),
      ontimeout: () => cb(new Error('timeout')),
    });
  }

  fab.addEventListener('click', () => {
    fab.classList.add('busy');
    fab.textContent = '正在连接本机服务…';
    ping(err => {
      fab.classList.remove('busy');
      fab.textContent = '📚 生成真题合订本';
      if (err) {
        say('没有连上本机服务。\n请先在终端里运行：\n<code>python -m hebing serve</code>\n然后回到这里再点一次。', 12000);
        return;
      }
      // 服务在：打开工作台，自动扫描下载目录
      const url = HOST + '/?auto=1';
      if (typeof GM_openInTab === 'function') GM_openInTab(url, { active: true });
      else window.open(url, '_blank');
    });
  });
})();
