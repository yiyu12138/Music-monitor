/* 网易云音乐页面逻辑（与 script.js 共用播放条、对话框、主题） */
(function () {
    'use strict';

    const $ = (sel, root = document) => root.querySelector(sel);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const alertBox = (o) => (window.uiAlert ? window.uiAlert(o) : Promise.resolve(alert(typeof o === 'string' ? o : o.message)));
    const confirmBox = (o) => (window.uiConfirm ? window.uiConfirm(o) : Promise.resolve(confirm(typeof o === 'string' ? o : o.message)));
    const fmtTime = (s) => { s = Math.floor(s || 0); return s > 0 ? Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0') : '--:--'; };
    const fmtSize = (b) => { if (!b) return ''; const u = ['B', 'KB', 'MB', 'GB']; let i = 0; while (b >= 1024 && i < 3) { b /= 1024; i++; } return b.toFixed(i ? 1 : 0) + ' ' + u[i]; };
    const thumb = (url, n = 120) => url ? url + (url.includes('?') ? '&' : '?') + 'param=' + n + 'y' + n : '';

    async function api(url, opt = {}) {
        if (opt.json !== undefined) {
            opt.body = JSON.stringify(opt.json);
            opt.headers = { 'Content-Type': 'application/json' };
            delete opt.json;
        }
        const r = await fetch(url, opt);
        let d = null;
        try { d = await r.json(); } catch (e) { /* 非 JSON */ }
        if (!r.ok) {
            const err = new Error((d && d.detail) || ('HTTP ' + r.status));
            err.status = r.status;
            throw err;
        }
        return d;
    }

    function toast(msg, tone = 'info') {
        let host = $('#toast-host');
        if (!host) { host = document.createElement('div'); host.id = 'toast-host'; document.body.appendChild(host); }
        const el = document.createElement('div');
        el.className = 'toast-pill tone-' + tone;
        const icon = { info: 'bi-info-circle', success: 'bi-check-circle-fill', warning: 'bi-exclamation-triangle-fill', danger: 'bi-x-circle-fill' }[tone];
        el.innerHTML = '<i class="bi ' + icon + '"></i><span>' + esc(msg) + '</span>';
        host.appendChild(el);
        requestAnimationFrame(() => el.classList.add('show'));
        setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, 3200);
    }
    window.toast = toast;

    const state = {
        logged: false, account: null, playlists: [], songs: [], pid: null, pname: '',
        listDir: '', loginMode: 'qr', qrKey: null, qrTimer: null, smsTimer: null, inited: false,
        taskTimer: null, filter: '',
    };

    // ======================= 登录 =======================
    async function refreshStatus() {
        let d;
        try { d = await api('/api/ncm/status'); } catch (e) { d = { logged_in: false }; }
        state.logged = d.logged_in;
        state.account = d.account;
        const chip = $('#ncm-account');
        if (state.logged) {
            chip.innerHTML =
                '<img src="/api/ncm/avatar" alt="" onerror="this.replaceWith(Object.assign(document.createElement(\'i\'),{className:\'bi bi-person-circle\'}))">' +
                '<span class="acc-name">' + esc(d.account.nickname) + '</span>' +
                (d.account.vip_type ? '<span class="vip-tag">VIP</span>' : '') +
                '<button class="btn btn-sm btn-ghost" id="ncm-logout" title="退出网易云登录"><i class="bi bi-box-arrow-right"></i></button>';
            $('#ncm-logout').onclick = logout;
            $('#ncm-login-panel').hidden = true;
            $('#ncm-workspace').hidden = false;
            stopQr();
            loadPlaylists();
        } else {
            chip.innerHTML = '<span class="text-soft"><i class="bi bi-person"></i> 未登录</span>';
            $('#ncm-login-panel').hidden = false;
            $('#ncm-workspace').hidden = true;
            setLoginMode(state.loginMode);
        }
    }

    function setLoginMode(mode) {
        state.loginMode = mode;
        document.querySelectorAll('#ncm-login-tabs [data-mode]').forEach(b => {
            const on = b.dataset.mode === mode;
            b.classList.toggle('active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        document.querySelectorAll('#ncm-login-panel .login-pane').forEach(p => { p.hidden = p.dataset.pane !== mode; });
        if (mode === 'qr') startQr(); else stopQr();
    }

    function stopQr() { if (state.qrTimer) clearInterval(state.qrTimer); state.qrTimer = null; }

    async function startQr() {
        stopQr();
        const box = $('#ncm-qr');
        box.classList.remove('expired', 'scanned');
        box.innerHTML = '<div class="spinner-border spinner-border-sm"></div>';
        $('#ncm-qr-msg').textContent = '正在生成二维码…';
        try {
            const d = await api('/api/ncm/qr');
            state.qrKey = d.key;
            box.innerHTML = '';
            if (window.QRCode) {
                new window.QRCode(box, { text: d.url, width: 176, height: 176, colorDark: '#111827', colorLight: '#ffffff', correctLevel: window.QRCode.CorrectLevel.M });
            } else {
                box.innerHTML = '<img width="176" height="176" alt="二维码" src="https://api.qrserver.com/v1/create-qr-code/?size=176x176&data=' + encodeURIComponent(d.url) + '">';
            }
            $('#ncm-qr-msg').textContent = '打开网易云音乐 App，扫一扫登录';
            state.qrTimer = setInterval(checkQr, 2000);
        } catch (e) {
            box.innerHTML = '<i class="bi bi-shield-exclamation"></i>';
            $('#ncm-qr-msg').innerHTML = esc(e.message) + '<br><a href="#" id="ncm-qr-again">重试</a> · 或改用 <a href="#" data-goto="phone">手机号</a> / <a href="#" data-goto="cookie">Cookie</a>';
            const again = $('#ncm-qr-again');
            if (again) again.onclick = (ev) => { ev.preventDefault(); startQr(); };
        }
    }

    async function checkQr() {
        if (!state.qrKey) return;
        try {
            const d = await api('/api/ncm/qr/check?key=' + encodeURIComponent(state.qrKey));
            const box = $('#ncm-qr');
            if (d.code === 800) {
                stopQr();
                box.classList.add('expired');
                $('#ncm-qr-msg').innerHTML = '二维码已过期，<a href="#" id="ncm-qr-again">点击刷新</a>';
                $('#ncm-qr-again').onclick = (e) => { e.preventDefault(); startQr(); };
            } else if (d.code === 802) {
                box.classList.add('scanned');
                $('#ncm-qr-msg').textContent = (d.nickname ? d.nickname + '，' : '') + '已扫码，请在手机上点击确认';
            } else if (d.code === 803) {
                stopQr();
                toast(d.message || '登录成功', 'success');
                refreshStatus();
            } else if (d.code === 8821 || d.code === -462) {
                stopQr();
                box.classList.add('expired');
                $('#ncm-qr-msg').innerHTML = '当前网络被网易云风控（' + esc(d.message || '需要安全验证') + '）<br>建议改用 <a href="#" data-goto="cookie">Cookie 登录</a>，或稍后 <a href="#" id="ncm-qr-again">重试</a>';
                const again = $('#ncm-qr-again');
                if (again) again.onclick = (ev) => { ev.preventDefault(); startQr(); };
            }
        } catch (e) { /* 网络抖动忽略 */ }
    }

    function riskHint(d) {
        return (d && d.status === 'risk')
            ? '\n\n网易云对当前网络环境触发了安全验证。可以稍等几分钟再试，或者切到「Cookie」方式登录（100% 可用）。'
            : '';
    }

    async function sendSms() {
        const phone = $('#ncm-phone').value.trim();
        const ctcode = $('#ncm-ctcode').value.trim() || '86';
        if (!/^\d{6,15}$/.test(phone)) { toast('请输入正确的手机号', 'warning'); $('#ncm-phone').focus(); return; }
        const btn = $('#ncm-sms-btn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
            const d = await api('/api/ncm/login/sms', { method: 'POST', json: { phone, ctcode } });
            if (d.status === 'success') {
                toast(d.message, 'success');
                $('#ncm-captcha').focus();
                let left = 60;
                btn.textContent = left + ' 秒';
                state.smsTimer = setInterval(() => {
                    left--;
                    if (left <= 0) { clearInterval(state.smsTimer); btn.disabled = false; btn.textContent = '重新发送'; }
                    else btn.textContent = left + ' 秒';
                }, 1000);
                return;
            }
            alertBox({ title: '验证码发送失败', message: d.message + riskHint(d), tone: 'warning' });
        } catch (e) {
            alertBox({ title: '验证码发送失败', message: e.message, tone: 'danger' });
        }
        btn.disabled = false;
        btn.textContent = '获取验证码';
    }

    async function phoneLogin(e) {
        if (e) e.preventDefault();
        const phone = $('#ncm-phone').value.trim();
        const ctcode = $('#ncm-ctcode').value.trim() || '86';
        const usePwd = $('#ncm-use-pwd').checked;
        const captcha = $('#ncm-captcha').value.trim();
        const password = $('#ncm-password').value;
        if (!/^\d{6,15}$/.test(phone)) { toast('请输入正确的手机号', 'warning'); return; }
        if (usePwd ? !password : !/^\d{4,6}$/.test(captcha)) { toast(usePwd ? '请输入密码' : '请输入短信验证码', 'warning'); return; }
        const btn = $('#ncm-phone-submit');
        btn.disabled = true;
        const label = btn.innerHTML;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 登录中';
        try {
            const d = await api('/api/ncm/login/phone', { method: 'POST', json: usePwd ? { phone, ctcode, password } : { phone, ctcode, captcha } });
            if (d.status === 'success') {
                toast(d.message, 'success');
                $('#ncm-password').value = '';
                $('#ncm-captcha').value = '';
                refreshStatus();
            } else {
                alertBox({ title: '登录失败', message: d.message + riskHint(d), tone: d.status === 'risk' ? 'warning' : 'danger' });
            }
        } catch (err) {
            alertBox({ title: '登录失败', message: err.message, tone: 'danger' });
        } finally {
            btn.disabled = false;
            btn.innerHTML = label;
        }
    }

    async function cookieLogin(e) {
        if (e) e.preventDefault();
        const cookie = $('#ncm-cookie').value.trim();
        if (!cookie) { toast('请粘贴 Cookie 或 MUSIC_U 的值', 'warning'); return; }
        try {
            const d = await api('/api/ncm/login/cookie', { method: 'POST', json: { cookie } });
            $('#ncm-cookie').value = '';
            toast(d.message || '登录成功', 'success');
            refreshStatus();
        } catch (err) {
            alertBox({ title: '登录失败', message: err.message, tone: 'danger' });
        }
    }

    async function logout() {
        const ok = await confirmBox({ title: '退出网易云登录', message: '退出后自动监控会暂停，直到重新登录。', confirmText: '退出', tone: 'warning' });
        if (!ok) return;
        await api('/api/ncm/logout', { method: 'POST' });
        state.pid = null;
        $('#ncm-playlists').innerHTML = '';
        refreshStatus();
    }

    // ======================= 歌单 =======================
    async function loadPlaylists() {
        const box = $('#ncm-playlists');
        if (!state.playlists.length) box.innerHTML = skeleton(6, 'pl');
        try {
            state.playlists = await api('/api/ncm/playlists');
            renderPlaylists();
        } catch (e) {
            if (e.status === 401) return refreshStatus();
            box.innerHTML = emptyState('bi-exclamation-triangle', '获取歌单失败：' + e.message);
        }
    }

    function renderPlaylists() {
        const box = $('#ncm-playlists');
        const item = (p) =>
            '<div class="list-group-item' + (p.id === state.pid ? ' active' : '') + (p.monitored ? ' monitored' : '') + '">' +
            '<a href="#" class="playlist-item flex-grow-1" data-id="' + p.id + '" data-name="' + esc(p.name) + '">' +
            '<span class="pl-icon"><i class="bi ' + (p.special === 5 ? 'bi-heart' : 'bi-music-note-list') + '"></i>' +
            (p.cover ? '<img class="pl-cover" src="' + esc(thumb(p.cover, 80)) + '" alt="" loading="lazy" onerror="this.remove()">' : '') + '</span>' +
            '<span class="pl-name flex-grow-1" title="' + esc(p.name) + '">' + esc(p.special === 5 ? '我喜欢的音乐' : p.name) + '</span>' +
            '<span class="badge">' + p.count + '首</span></a>' +
            '<button class="btn btn-sm monitor-btn' + (p.monitored ? ' active' : '') + '" data-id="' + p.id + '" aria-pressed="' + p.monitored + '" title="' +
            (p.monitored ? '已开启监控：新增歌曲会自动下载，点击取消' : '开启监控：该歌单有新增歌曲时自动下载') + '">' +
            (p.monitored ? '<i class="bi bi-broadcast"></i><span class="monitor-text">监控中</span><span class="monitor-dot"></span>' : '<i class="bi bi-eye"></i><span class="monitor-text">监控</span>') +
            '</button></div>';
        const mine = state.playlists.filter(p => !p.subscribed);
        const fav = state.playlists.filter(p => p.subscribed);
        let html = '';
        if (mine.length) html += '<h6><i class="bi bi-music-note-list"></i> 创建的歌单</h6><div class="list-group mb-3">' + mine.map(item).join('') + '</div>';
        if (fav.length) html += '<h6><i class="bi bi-bookmark-heart"></i> 收藏的歌单</h6><div class="list-group">' + fav.map(item).join('') + '</div>';
        box.innerHTML = html || emptyState('bi-collection', '还没有歌单');
    }

    async function onPlaylistClick(e) {
        const mon = e.target.closest('.monitor-btn');
        if (mon) {
            e.preventDefault();
            mon.disabled = true;
            mon.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
            try {
                const d = await api('/api/ncm/monitor/' + mon.dataset.id, { method: 'POST' });
                toast(d.message, d.monitored ? 'success' : 'info');
            } catch (err) { toast(err.message, 'danger'); }
            const p = state.playlists.find(x => x.id === mon.dataset.id);
            if (p) p.monitored = !p.monitored;
            await loadPlaylists();
            return;
        }
        const a = e.target.closest('.playlist-item');
        if (!a) return;
        e.preventDefault();
        openPlaylist(a.dataset.id, a.dataset.name);
    }

    async function openPlaylist(pid, name) {
        state.pid = pid;
        state.pname = name;
        $('#ncm-search').value = '';
        document.querySelectorAll('#ncm-playlists .list-group-item').forEach(x =>
            x.classList.toggle('active', !!x.querySelector('.playlist-item[data-id="' + pid + '"]')));
        const box = $('#ncm-songs');
        box.innerHTML = skeleton(8, 'song');
        try {
            const d = await api('/api/ncm/playlist/' + pid);
            state.songs = d.songs;
            state.listDir = d.dir;
            renderSongs({ title: d.name, icon: 'bi-music-note-list', cover: d.cover, playlist: true });
        } catch (err) {
            box.innerHTML = emptyState('bi-exclamation-triangle', err.message);
        }
    }

    async function search() {
        const kw = $('#ncm-search').value.trim();
        if (!kw) return;
        state.pid = null;
        state.pname = '';
        document.querySelectorAll('#ncm-playlists .list-group-item').forEach(x => x.classList.remove('active'));
        $('#ncm-songs').innerHTML = skeleton(8, 'song');
        try {
            const d = await api('/api/ncm/search?keyword=' + encodeURIComponent(kw));
            state.songs = d.songs;
            state.listDir = d.dir;
            renderSongs({ title: '搜索：' + kw, icon: 'bi-search', playlist: false });
        } catch (err) {
            $('#ncm-songs').innerHTML = emptyState('bi-exclamation-triangle', err.message);
        }
    }

    // ======================= 歌曲列表 =======================
    function songButton(s) {
        switch (s.status) {
            case 'completed':
                return '<button class="btn btn-success btn-sm download-btn" data-act="redl" title="已下载，点击可重新下载">已下载</button>';
            case 'downloading':
                return '<button class="btn btn-secondary btn-sm download-btn" disabled>' + (s.progress || 0) + '%</button>';
            case 'queued':
                return '<button class="btn btn-secondary btn-sm download-btn" disabled>队列中</button>';
            case 'local':
                return '<button class="btn btn-sm download-btn btn-local-file" data-act="redl" title="本地已有，点击可覆盖重新下载">重新下载</button>';
            case 'failed':
                return '<button class="btn btn-danger btn-sm download-btn" data-act="dl" title="下载失败，点击重试">重试</button>';
            case 'cancelled':
                return '<button class="btn btn-outline-danger btn-sm download-btn" data-act="dl">已取消</button>';
            default:
                return s.playable === false
                    ? '<button class="btn btn-sm download-btn btn-disabled-soft" disabled title="该歌曲暂无版权">无版权</button>'
                    : '<button class="btn btn-primary btn-sm download-btn" data-act="dl">下载</button>';
        }
    }

    function songRow(s, i) {
        const singers = s.singer.map(a => a.name).join(' / ');
        const pills = [];
        if (s.fee === 1) pills.push('<span class="pill pill-vip">VIP</span>');
        if (s.local) pills.push('<span class="pill pill-local" title="' + esc(s.local.path) + '"><i class="bi bi-check2-circle"></i>本地已存在</span>',
            '<span class="pill pill-quality">' + esc(s.quality || s.local.ext) + '</span>',
            '<span class="pill pill-size">' + fmtSize(s.local.size) + '</span>');
        return '<li class="list-group-item song-item' + (s.playable === false ? ' is-disabled' : '') + '" data-i="' + i + '" data-song-mid="ncm:' + s.id + '">' +
            '<span class="song-index">' + (i + 1) + '</span>' +
            '<span class="song-cover-wrap"><i class="bi bi-music-note"></i>' +
            (s.cover ? '<img class="song-cover" src="' + esc(thumb(s.cover, 80)) + '" alt="" loading="lazy" onerror="this.remove()">' : '') + '</span>' +
            '<div class="song-title-wrap"><div class="song-title" title="' + esc(s.name) + '">' + esc(s.name) + '</div>' +
            '<div class="song-sub"><span class="song-singer">' + esc(singers) + '</span>' +
            (s.album ? '<span class="song-album" title="专辑：' + esc(s.album) + '">· ' + esc(s.album) + '</span>' : '') +
            pills.join('') + '</div></div>' +
            '<span class="song-duration">' + fmtTime(s.interval) + '</span>' +
            '<div class="song-actions"><button class="btn btn-icon play-btn" title="试听"' + (s.playable === false ? ' disabled' : '') + '><i class="bi bi-play-fill"></i></button>' +
            '<span class="btn-slot">' + songButton(s) + '</span></div></li>';
    }

    function renderSongs(meta) {
        const box = $('#ncm-songs');
        state.meta = meta;
        if (!state.songs.length) {
            box.innerHTML = emptyState(meta.playlist ? 'bi-inbox' : 'bi-search', meta.playlist ? '这个歌单里没有歌曲' : '没有找到相关歌曲');
            return;
        }
        const localCount = state.songs.filter(s => s.local).length;
        box.innerHTML =
            '<div class="song-toolbar">' +
            '<div class="toolbar-title"><i class="bi ' + meta.icon + '"></i><span class="text-truncate" title="' + esc(meta.title) + '">' + esc(meta.title) + '</span>' +
            '<span class="toolbar-count">' + state.songs.length + ' 首' + (localCount ? ' · 本地 ' + localCount : '') + '</span></div>' +
            '<div class="toolbar-actions">' +
            '<input class="form-control form-control-sm toolbar-filter" id="ncm-filter" placeholder="筛选…" value="' + esc(state.filter) + '">' +
            (meta.playlist ? '<button class="btn btn-success btn-sm" id="ncm-dl-all" title="保存到 ' + esc(state.listDir) + '"><i class="bi bi-download"></i> 全部下载</button>' : '') +
            '</div></div>' +
            '<ul class="list-group list-group-flush">' + state.songs.map(songRow).join('') + '</ul>';
        if (meta.playlist) $('#ncm-dl-all').onclick = downloadAll;
        $('#ncm-filter').oninput = (e) => { state.filter = e.target.value; applyFilter(); };
        applyFilter();
        if (window.MusicPlayer) window.MusicPlayer.sync();
    }

    function applyFilter() {
        const q = state.filter.trim().toLowerCase();
        document.querySelectorAll('#ncm-songs .song-item').forEach(li => {
            const s = state.songs[+li.dataset.i];
            const hay = (s.name + ' ' + s.singer.map(a => a.name).join(' ') + ' ' + s.album).toLowerCase();
            li.hidden = !!q && !hay.includes(q);
        });
    }

    async function downloadAll(e) {
        const btn = e.currentTarget;
        btn.disabled = true;
        const label = btn.innerHTML;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 加入队列中';
        try {
            const d = await api('/api/ncm/playlist/' + state.pid + '/download', { method: 'POST' });
            alertBox({ title: '已加入下载队列', message: d.message, rows: [{ label: '保存到', value: d.dir }], tone: 'success' });
            loadTasks();
        } catch (err) { toast(err.message, 'danger'); }
        btn.disabled = false;
        btn.innerHTML = label;
    }

    async function onSongClick(e) {
        const li = e.target.closest('.song-item');
        if (!li) return;
        const s = state.songs[+li.dataset.i];
        if (e.target.closest('.play-btn')) return play(s);
        const btn = e.target.closest('.download-btn[data-act]');
        if (!btn) return;
        let force = false;
        if (btn.dataset.act === 'redl') {
            const rows = s.local ? [
                { label: '文件', value: s.local.filename, title: s.local.path },
                { label: '大小', value: fmtSize(s.local.size) },
            ] : [{ label: '歌曲', value: s.name }];
            const ok = await confirmBox({ title: '本地已有该歌曲', message: '重新下载会覆盖本地这份文件。', rows, confirmText: '重新下载', tone: 'warning' });
            if (!ok) return;
            force = true;
        }
        btn.disabled = true;
        btn.textContent = '队列中';
        try {
            const d = await api('/api/ncm/download', { method: 'POST', json: { song: s, force, playlist_id: state.pid || '', playlist_name: state.pname || '' } });
            if (d.status === 'local_exists') {
                s.status = 'local'; s.local = d.local;
                li.outerHTML = songRow(s, +li.dataset.i);
                toast('本地已存在，已跳过：' + d.local.filename, 'info');
                return;
            }
            if (d.status === 'skipped') toast(d.message, 'info');
            s.status = 'queued';
            li.querySelector('.btn-slot').innerHTML = songButton(s);
            loadTasks();
        } catch (err) {
            toast(err.message, 'danger');
            li.querySelector('.btn-slot').innerHTML = songButton(s);
        }
    }

    function play(s) {
        if (!window.MusicPlayer) return;
        window.MusicPlayer.playExternal({
            key: 'ncm:' + s.id,
            title: s.name,
            artist: s.singer.map(a => a.name).join(' / '),
            cover: thumb(s.cover, 120),
            resolve: async () => {
                const d = await api('/api/ncm/play/' + s.id);
                if (d.trial) toast('该歌曲需要 VIP，只能试听片段', 'warning');
                return d;
            },
        });
    }

    // ======================= 下载任务 =======================
    const ST = {
        queued: ['secondary', '排队中'], downloading: ['primary', '下载中'], completed: ['success', '已完成'],
        failed: ['danger', '失败'], cancelled: ['dark', '已取消'],
    };

    async function loadTasks() {
        if (document.body.dataset.page !== 'netease' || document.hidden) return;
        let list;
        try { list = await api('/api/ncm/tasks'); } catch (e) { return; }
        const map = Object.fromEntries(list.map(t => [t.id, t]));
        // 同步歌曲列表按钮
        document.querySelectorAll('#ncm-songs .song-item').forEach(li => {
            const s = state.songs[+li.dataset.i];
            const t = map[s.id];
            if (!t) return;
            let st = t.status;
            if (st === 'completed' && t.skipped) st = 'local';
            if (st !== s.status || t.progress !== s.progress) {
                s.status = st; s.progress = t.progress;
                const slot = li.querySelector('.btn-slot');
                if (slot) slot.innerHTML = songButton(s);
            }
        });
        const active = list.filter(t => t.status === 'queued' || t.status === 'downloading');
        const failed = list.filter(t => t.status === 'failed' || t.status === 'cancelled');
        const done = list.filter(t => t.status === 'completed');
        $('#ncm-active-count').textContent = active.length + failed.length;
        $('#ncm-done-count').textContent = done.length;
        $('#ncm-retry-failed').hidden = !failed.length;
        $('#ncm-active').innerHTML = (active.length + failed.length)
            ? active.concat(failed).map(taskRow).join('')
            : emptyState('bi-cloud-check', '没有进行中的任务', true);
        $('#ncm-done').innerHTML = done.length
            ? done.slice(0, 100).map(taskRow).join('') + (done.length > 100 ? '<li class="list-more">仅显示最近 100 条，共 ' + done.length + ' 条</li>' : '')
            : emptyState('bi-inbox', '还没有完成的下载', true);
    }

    function taskRow(t) {
        const [c, label] = ST[t.status] || ['light', t.status];
        let act = '';
        if (t.status === 'queued' || t.status === 'downloading') {
            act = '<button class="btn btn-outline-warning t-act" data-a="cancel" data-id="' + t.id + '" title="取消"><i class="bi bi-x-lg"></i></button>';
        } else {
            if (t.status !== 'completed') act += '<button class="btn btn-outline-info t-act" data-a="retry" data-id="' + t.id + '" title="重试"><i class="bi bi-arrow-clockwise"></i></button>';
            act += '<button class="btn btn-outline-danger t-act" data-a="remove" data-id="' + t.id + '" title="从列表移除"><i class="bi bi-trash"></i></button>';
        }
        const sub = [];
        if (t.quality) sub.push('<span class="pill pill-quality">' + esc(t.quality) + '</span>');
        if (t.skipped) sub.push('<span class="pill pill-local">本地已存在</span>');
        if (t.file_size) sub.push('<span class="pill pill-size">' + fmtSize(t.file_size) + '</span>');
        if (t.source) sub.push('<span class="task-source" title="来源">' + esc(t.source) + '</span>');
        return '<li class="list-group-item task-card status-' + t.status + '">' +
            '<span class="task-cover">' + (t.cover ? '<img src="' + esc(thumb(t.cover, 64)) + '" alt="" loading="lazy" onerror="this.remove()">' : '') + '<i class="bi bi-music-note"></i></span>' +
            '<div class="task-body"><div class="task-line"><span class="task-name text-truncate" title="' + esc(t.song_name) + '">' + esc(t.song_name) + '</span>' +
            '<span class="badge bg-' + c + '">' + label + '</span></div>' +
            (t.status === 'downloading' ? '<div class="progress mt-1"><div class="progress-bar" style="width:' + (t.progress || 0) + '%">' + (t.progress || 0) + '%</div></div>' : '') +
            (sub.length ? '<div class="task-meta">' + sub.join('') + '</div>' : '') +
            (t.error && t.status !== 'completed' ? '<div class="task-reason text-danger">' + esc(t.error) + '</div>' : '') +
            '</div><div class="task-actions">' + act + '</div></li>';
    }

    async function onTaskClick(e) {
        const b = e.target.closest('.t-act');
        if (!b) return;
        b.disabled = true;
        try { await api('/api/ncm/task/' + b.dataset.id + '/' + b.dataset.a, { method: 'POST' }); }
        catch (err) { toast(err.message, 'danger'); }
        loadTasks();
    }

    // ======================= 小组件 =======================
    function emptyState(icon, text, compact) {
        return '<div class="empty-state' + (compact ? ' compact' : '') + '"><i class="bi ' + icon + '"></i><div>' + esc(text) + '</div></div>';
    }

    function skeleton(n, kind) {
        return '<div class="skeleton-list">' + Array.from({ length: n }, () =>
            '<div class="skeleton-row ' + kind + '"><span class="sk sk-img"></span><span class="sk-lines"><span class="sk sk-l1"></span><span class="sk sk-l2"></span></span></div>').join('') + '</div>';
    }

    // ======================= 初始化 =======================
    function init() {
        if (state.inited) return;
        state.inited = true;
        document.querySelectorAll('#ncm-login-tabs [data-mode]').forEach(b => { b.onclick = () => setLoginMode(b.dataset.mode); });
        document.addEventListener('click', (e) => {
            const g = e.target.closest('[data-goto]');
            if (g && g.closest('#netease-content')) { e.preventDefault(); setLoginMode(g.dataset.goto); }
        });
        $('#ncm-sms-btn').onclick = sendSms;
        $('#ncm-phone-form').onsubmit = phoneLogin;
        $('#ncm-cookie-form').onsubmit = cookieLogin;
        $('#ncm-use-pwd').onchange = (e) => {
            $('#ncm-captcha-group').hidden = e.target.checked;
            $('#ncm-password-group').hidden = !e.target.checked;
        };
        $('#ncm-playlists').addEventListener('click', onPlaylistClick);
        $('#ncm-songs').addEventListener('click', onSongClick);
        $('#ncm-active').addEventListener('click', onTaskClick);
        $('#ncm-done').addEventListener('click', onTaskClick);
        $('#ncm-search-btn').onclick = search;
        $('#ncm-search').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); search(); } });
        $('#ncm-retry-failed').onclick = async () => { const d = await api('/api/ncm/tasks/retry_failed', { method: 'POST' }); toast(d.message, 'success'); loadTasks(); };
        $('#ncm-clear-done').onclick = async () => {
            const ok = await confirmBox({ title: '清理任务记录', message: '只清理列表记录，不会删除已下载的文件。', confirmText: '清理', tone: 'warning' });
            if (!ok) return;
            await api('/api/ncm/tasks/clear', { method: 'POST' });
            loadTasks();
        };
        refreshStatus();
        loadTasks();
        state.taskTimer = setInterval(loadTasks, 2000);
    }

    document.addEventListener('page:change', (e) => {
        if (e.detail === 'netease') { init(); loadTasks(); }
        else stopQr();
    });
    document.addEventListener('DOMContentLoaded', () => {
        if (document.body.dataset.page === 'netease') init();
    });
})();
