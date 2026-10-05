/* 下载源（洛雪音乐 LX Music 自定义源）管理 */
(function () {
    const $ = (s, r = document) => r.querySelector(s);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const alertMsg = (m) => (window.uiAlert ? window.uiAlert(m) : alert(m));
    const confirmMsg = (m) => (window.uiConfirm ? window.uiConfirm(m) : Promise.resolve(confirm(m)));
    const HIT = new Set(['tx', 'wy']);  // 本项目会用到的两个平台

    async function api(path, opts = {}) {
        const res = await fetch('/api/sources' + path, opts);
        let data = {};
        try { data = await res.json(); } catch (e) { /* ignore */ }
        if (!res.ok) throw new Error(data.detail || data.message || ('HTTP ' + res.status));
        return data;
    }
    const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

    function render(list) {
        const box = $('#src-list');
        if (!box) return;
        if (!list.length) {
            box.innerHTML = '<div class="src-empty"><i class="bi bi-plug"></i><div>还没有添加下载源</div><small>未添加时，下载全部走账号官方渠道（与之前一致）</small></div>';
            return;
        }
        box.innerHTML = list.map((s, i) => {
            const state = !s.enabled ? '<span class="src-state off">已停用</span>'
                : s.loaded ? '<span class="src-state ok">运行中</span>'
                : '<span class="src-state err">加载失败</span>';
            const plats = (s.platforms || []).map(p =>
                '<span class="src-plat' + (HIT.has(p.key) ? ' hit' : '') + '" title="' + esc((p.qualitys || []).join(' / ')) + '">' + esc(p.name) + '</span>').join('');
            const usable = (s.platforms || []).some(p => HIT.has(p.key));
            const hint = s.loaded && !usable ? '<div class="src-err" style="color:var(--text-2)">该源没有提供 QQ 音乐 / 网易云，本项目用不上</div>' : '';
            return '<div class="src-item' + (s.enabled ? '' : ' off') + '" data-id="' + esc(s.id) + '">'
                + '<div class="src-order"><button type="button" data-act="up" title="上移"' + (i === 0 ? ' disabled' : '') + '><i class="bi bi-chevron-up"></i></button>'
                + '<span class="src-rank">' + (i + 1) + '</span>'
                + '<button type="button" data-act="down" title="下移"' + (i === list.length - 1 ? ' disabled' : '') + '><i class="bi bi-chevron-down"></i></button></div>'
                + '<div class="src-main"><div class="src-title">' + esc(s.name) + (s.version ? ' <span class="src-ver">v' + esc(s.version) + '</span>' : '') + state + '</div>'
                + '<div class="src-desc" title="' + esc(s.description || s.url || '') + '">' + esc(s.description || s.author || s.url || '自定义源') + '</div>'
                + (plats ? '<div class="src-plats">' + plats + '</div>' : '')
                + (s.error && s.enabled ? '<div class="src-err">' + esc(s.error) + '</div>' : '') + hint
                + '</div>'
                + '<div class="src-ops">'
                + '<div class="form-check form-switch m-0" title="启用 / 停用"><input class="form-check-input" type="checkbox" data-act="toggle"' + (s.enabled ? ' checked' : '') + '></div>'
                + '<button type="button" class="btn btn-ghost btn-sm" data-act="reload" title="' + (s.url ? '从链接更新并重新加载' : '重新加载') + '"><i class="bi bi-arrow-repeat"></i></button>'
                + '<button type="button" class="btn btn-ghost btn-sm text-danger" data-act="del" title="删除"><i class="bi bi-trash"></i></button>'
                + '</div></div>';
        }).join('');
    }

    async function load() {
        try {
            const data = await api('');
            const un = $('#sources-unavailable');
            if (un) un.hidden = !!data.available;
            render(data.sources || []);
        } catch (e) {
            console.error('加载下载源失败', e);
        }
    }

    async function withBusy(btn, fn) {
        if (btn) { btn.disabled = true; btn.dataset.html = btn.innerHTML; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 处理中'; }
        try { await fn(); } finally { if (btn) { btn.disabled = false; btn.innerHTML = btn.dataset.html; } }
    }

    async function addBy(kind, value, btn) {
        await withBusy(btn, async () => {
            try {
                let data;
                if (kind === 'file') {
                    const fd = new FormData(); fd.append('file', value);
                    data = await api('/upload', { method: 'POST', body: fd });
                } else {
                    data = await api('', json('POST', kind === 'url' ? { url: value } : { script: value }));
                }
                if ($('#src-url')) $('#src-url').value = '';
                if ($('#src-script')) $('#src-script').value = '';
                await load();
                const s = data.source || {};
                const plats = (s.platforms || []).map(p => p.name).join('、') || '未声明';
                alertMsg((data.message || '已添加') + '\n支持平台：' + plats);
            } catch (e) {
                alertMsg(/^添加失败/.test(e.message) ? e.message : ('添加失败：' + e.message));
            }
        });
    }

    function bind() {
        const card = $('#cfg-sources');
        if (!card || card.dataset.bound) return;
        card.dataset.bound = '1';

        card.querySelectorAll('#src-add-tabs [data-mode]').forEach(b => b.addEventListener('click', () => {
            card.querySelectorAll('#src-add-tabs [data-mode]').forEach(x => x.classList.toggle('active', x === b));
            card.querySelectorAll('.src-pane').forEach(p => { p.hidden = p.dataset.pane !== b.dataset.mode; });
        }));

        $('#src-add-url').addEventListener('click', (e) => {
            const v = ($('#src-url').value || '').trim();
            if (!v) return alertMsg('请先填写脚本链接');
            addBy('url', v, e.currentTarget);
        });
        $('#src-url').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); $('#src-add-url').click(); } });
        $('#src-add-script').addEventListener('click', (e) => {
            const v = ($('#src-script').value || '').trim();
            if (!v) return alertMsg('请先粘贴脚本内容');
            addBy('paste', v, e.currentTarget);
        });
        const file = $('#src-file'), drop = card.querySelector('.src-drop');
        file.addEventListener('change', () => { if (file.files[0]) addBy('file', file.files[0]); file.value = ''; });
        ['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('dragover'); }));
        ['dragleave', 'drop'].forEach(t => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('dragover'); }));
        drop.addEventListener('drop', (e) => { const f = e.dataTransfer.files[0]; if (f) addBy('file', f); });

        $('#src-list').addEventListener('click', async (e) => {
            const el = e.target.closest('[data-act]');
            const item = e.target.closest('.src-item');
            if (!el || !item || el.dataset.act === 'toggle') return;
            const id = item.dataset.id, act = el.dataset.act;
            try {
                if (act === 'up' || act === 'down') {
                    const d = await api('/' + id + '/move', json('POST', { delta: act === 'up' ? -1 : 1 }));
                    render(d.sources || []);
                } else if (act === 'reload') {
                    await withBusy(el, async () => { const d = await api('/' + id + '/reload', json('POST')); await load(); alertMsg(d.message); });
                } else if (act === 'del') {
                    const name = item.querySelector('.src-title')?.firstChild?.textContent || '该源';
                    if (!(await confirmMsg('确定删除下载源「' + name.trim() + '」？'))) return;
                    await api('/' + id, { method: 'DELETE' });
                    await load();
                }
            } catch (err) { alertMsg(err.message); }
        });
        $('#src-list').addEventListener('change', async (e) => {
            const el = e.target.closest('[data-act="toggle"]');
            const item = e.target.closest('.src-item');
            if (!el || !item) return;
            try { await api('/' + item.dataset.id + '/toggle', json('POST', { enabled: el.checked })); await load(); }
            catch (err) { el.checked = !el.checked; alertMsg(err.message); }
        });
    }

    function init() { bind(); load(); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
    // 切到配置页时刷新一次状态
    window.addEventListener('hashchange', () => { if (location.hash === '#config') load(); });
    window.MusicSources = { reload: load };
})();
