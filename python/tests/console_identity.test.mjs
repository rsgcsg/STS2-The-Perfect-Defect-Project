import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.value = ''; this.dataset = {}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener() {}
  get options() { return this.children; }
}
function setup(mode = 'local', flow = '', cloudUrl = 'https://hub.example') {
  const nodes = new Map(['account-actions', 'device-scope', 'content', 'notice'].map(k => [k, new Element('div')]));
  const calls = [], navigations = [], timers = new Map();
  let now = Date.now(), timerId = 0;
  class Clock extends Date { static now() { return now; } }
  const context = vm.createContext({
    document: {body: {dataset: {mode, cloudUrl}}, getElementById: key => nodes.get(key),
      createElement: tag => new Element(tag), querySelector: () => null}, window: {},
    location: {assign(url) { navigations.push(url); }, search: '?view=connect&flow=' + flow}, history: {pushState() {}}, Date: Clock, URLSearchParams, AbortSignal,
    setTimeout: (fn, delay) => { const id = ++timerId; timers.set(id, {fn, delay}); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => new Promise(resolve => calls.push({url, options,
      answer: body => resolve({ok:true, json: async () => body})})),
  });
  vm.runInContext(readFileSync(new URL('../spireagent/console/identity.js', import.meta.url), 'utf8'), context);
  return {ui: context.window.SpireIdentity, nodes, calls, navigations, timers,
    now: () => now, advance: ms => {now += ms;},
    fireTimer() {
      const [id, {fn, delay}] = timers.entries().next().value;
      timers.delete(id); now += delay; return fn();
    },
  };
}
const person = (subject = 'one') => ({status: 'signed_in', csrf_token: 'csrf',
  principal: {subject, email: subject + '@example.test'}, devices: [{device_id: 'pc', name: 'Laptop'}]});

const flatten = element => [element.textContent || '', ...(element.children || []).map(flatten)].join(' ');
const descendants = element => [element, ...(element.children || []).flatMap(descendants)];
test('local-only boot retains account navigation without requesting cloud identity', async () => {
  const env = setup();
  assert.equal(env.ui.localOnly().status, 'local_only');
  assert.equal(env.calls.length, 0);
  assert.equal(env.nodes.get('device-scope').value, 'local');
  const login = descendants(env.nodes.get('account-actions')).find(n => n.textContent === '登录项目账号');
  assert.ok(login);
  await login.onclick();
  assert.deepEqual(env.navigations, ['?view=devices']);
  assert.equal(env.calls.length, 0);
});
test('local-only view selects local scope without clearing an existing signed-in account', async () => {
  const env = setup();
  const loading = env.ui.refresh(true);
  env.calls.shift().answer(person()); await loading;
  env.ui.ensureProjectScope();
  assert.equal(env.ui.isLocal(), false);
  env.ui.localOnly();
  assert.equal(env.ui.isLocal(), true);
  assert.match(flatten(env.nodes.get('account-actions')), /one@example.test/);
  assert.equal(env.calls.length, 0);
});

async function connectionPage(facts) {
  const env = setup('cloud', 'a'.repeat(32));
  const initial = env.ui.refresh(true); env.calls.shift().answer(person('current')); await initial;
  const rendering = env.ui.renderConnect();
  env.calls.shift().answer({status: 'pending', purpose: 'connect_existing_device', device_id: 'pc',
    device_name: 'Laptop', user_code: 'ABCDEFGH', approval_allowed: false, ...facts});
  return {...env, page: await rendering};
}

test('different account explains existing profile ownership and offers a real sign-out path', async () => {
  const {page, calls, navigations} = await connectionPage({approval_block_reason: 'different_account'});
  assert.match(flatten(page), /current@example.test/);
  assert.match(flatten(page), /原绑定账号重新连接/);
  assert.match(flatten(page), /沿用已有设备上传凭据/);
  assert.match(flatten(page), /即使是管理员/);
  assert.match(flatten(page), /切回刚才的工作台标签页/);
  assert.equal(descendants(page).some(n => n.textContent === '确认是我的连接，批准接入'), false);
  assert.match(flatten(page), /另一账号的工作台配置/);
  assert.doesNotMatch(flatten(page), /另一账号的电脑/);
  await descendants(page).find(n => n.textContent === '退出并更换登录账号').onclick();
  assert.deepEqual(navigations, ['/cdn-cgi/access/logout']);
  assert.equal(calls.length, 0);
  assert.equal(descendants(page).find(n => n.textContent === '查看当前账号的电脑').href, '?view=devices');
});

test('specific connection blocks do not invite permission bypass or invent an owner email', async () => {
  for (const [reason,phrase] of [['device_disabled','授权已停用'], ['proof_changed','旧凭据'],
    ['device_quota_reached','电脑名额已用完'], ['expired','连接请求已过期'],
    ['flow_invalidated','连接请求已失效']]) {
    const {page} = await connectionPage({approval_block_reason: reason});
    assert.match(flatten(page), new RegExp(phrase));
    assert.equal(descendants(page).some(n => n.textContent === '确认是我的连接，批准接入'), false);
    assert.equal(descendants(page).some(n => n.textContent === '退出并更换登录账号'), false);
  }
});

test('legacy Hub boolean remains compatible without guessing a specific block', async () => {
  const {page} = await connectionPage({});
  assert.match(flatten(page), /请先核对是否使用原绑定账号/);
  assert.doesNotMatch(flatten(page), /当前登录邮箱不是/);
  for (const extra of [{}, {approval_block_reason: null}]) {
    const allowed = await connectionPage({approval_allowed: true, ...extra});
    assert.ok(descendants(allowed.page).some(n => n.textContent === '确认是我的连接，批准接入'));
  }
});

test('local and cloud account pages explain profiles instead of unique physical computers', async () => {
  const meaning = /每条登记对应一个账号的工作台配置；同一台电脑可以有多条登记，修改名称不会合并账号或历史记录/;
  for (const mode of ['local', 'cloud']) {
    const env = setup(mode);
    const loading = env.ui.refresh(true);
    env.calls.shift().answer(mode === 'local' ? {status: 'signed_out', hub_configured: true} : person());
    await loading;
    const page = env.ui.renderDevices();
    assert.match(flatten(page), meaning);
    if (mode === 'local') {
      assert.ok(descendants(page).some(n => n.textContent === '连接名称'));
      assert.ok(descendants(page).some(n => n.textContent === '登录并连接本机'));
      assert.match(flatten(page), /登录项目账号后，可按权限访问团队工作区中的数据、模型、报告和其他已启用资源/);
      assert.match(flatten(page), /模型文件下载还需要这台电脑的设备授权有效/);
      assert.match(flatten(page), /不会自动加载模型或启动训练、游戏操作/);
      assert.match(flatten(page), /本机资料和本地模型状态仍可在未登录时查看/);
      assert.equal(env.calls.length, 0, 'rendering account guidance must not add a request');
    }
  }
  const {page} = await connectionPage({approval_allowed: true});
  assert.match(flatten(page), meaning);
  assert.match(flatten(page), /重新连接已有工作台配置/);
});

test('signed-in local project scope is labeled as team material without changing its protocol value', async () => {
  const env = setup();
  const loading = env.ui.refresh(true);
  env.calls.shift().answer({
    ...person(),
    principal: {subject: 'one', email: 'one@example.test', project_shared: true},
  });
  await loading;
  const option = env.nodes.get('device-scope').options.find(item => item.value === 'project');
  assert.equal(option.textContent, '团队资料 · 全部共享内容');
  assert.equal(env.nodes.get('device-scope').value, 'local');
  assert.equal(env.ui.isLocal(), true);
});

test('unconfigured local Hub explains unavailable team access without showing a login action', async () => {
  const env = setup('local', '', '');
  const loading = env.ui.refresh(true);
  env.calls.shift().answer({status: 'signed_out', hub_configured: false});
  await loading;
  const page = env.ui.renderDevices();
  assert.match(flatten(page), /项目 Hub 尚未配置，暂时无法访问团队工作区及其中的数据、模型和报告/);
  assert.match(flatten(page), /本机资料和本地模型状态仍可查看/);
  assert.equal(descendants(page).some(n => n.textContent === '登录项目账号'), false);
  assert.equal(env.calls.length, 0, 'rendering account guidance must not add a request');
});

test('account logout rejects an already in-flight identity response and retains local scope', async () => {
  const {ui, nodes, calls} = setup();
  const initial = ui.refresh(true); calls.shift().answer(person()); await initial;
  const pending = ui.refresh(true), old = calls.shift();
  const logout = nodes.get('account-actions').children.find(x => x.textContent === '退出网页账号');
  const exiting = logout.onclick();
  assert.equal(nodes.get('account-actions').children.some(x => x.textContent === 'one@example.test'), false);
  old.answer(person()); await pending;
  assert.equal(nodes.get('account-actions').children.some(x => x.textContent === 'one@example.test'), false);
  calls.shift().answer({remote_revoked: true});
  await new Promise(resolve => setImmediate(resolve));
  calls.shift().answer({status:'signed_out', csrf_token:'csrf'});
  await exiting;
  assert.equal(ui.isLocal(), true);
  assert.match(ui.context(), /anonymous/);
});

const pendingIdentity = env => ({status: 'signed_out', hub_configured: true, csrf_token: 'csrf',
  flow: {flow_id: 'a'.repeat(32), expires_at: env.now() / 1000 + 60,
    user_code: 'ABCDEFGH', approval_url: 'https://example.test/app/?view=connect'}});

test('reopened local flow resumes one approval poll and observes the approved account', async () => {
  const env = setup();
  const facts = pendingIdentity(env);
  const initial = env.ui.refresh(true); env.calls.shift().answer(facts); await initial;
  env.ui.renderDevices(); env.ui.renderDevices();
  const refresh = env.ui.refresh(true); env.calls.shift().answer(facts); await refresh;
  assert.equal(env.timers.size, 1);
  const polling = env.fireTimer();
  assert.equal(env.calls[0].url, '/api/identity/poll');
  assert.equal(env.calls[0].options.headers['X-CSRF-Token'], 'csrf');
  const manual = descendants(env.ui.renderDevices()).find(n => n.textContent === '检查绑定结果');
  await manual.onclick();
  assert.equal(env.calls.length, 1, 'manual checking shares the in-flight poll');
  env.calls.shift().answer({status: 'approved'}); await settled();
  assert.equal(env.calls[0].url, '/api/identity');
  env.calls.shift().answer(person()); await polling;
  assert.match(flatten(env.nodes.get('account-actions')), /one@example.test/);
  assert.match(env.nodes.get('notice').textContent, /登录与设备绑定完成/);
  assert.equal(env.ui.isLocal(), false);
  assert.equal(env.timers.size, 0);
});

test('approved polling waits for an older identity read then obtains fresh signed-in state', async () => {
  const env = setup(), facts = pendingIdentity(env);
  const initial = env.ui.refresh(true); env.calls.shift().answer(facts); await initial;
  const polling = env.fireTimer(), pollCall = env.calls.shift();
  const oldRefresh = env.ui.refresh(true), oldRead = env.calls.shift();
  pollCall.answer({status: 'approved'}); await settled();
  assert.equal(env.calls.length, 0);
  oldRead.answer(facts); await oldRefresh; await settled();
  assert.equal(env.calls[0].url, '/api/identity');
  env.calls.shift().answer(person()); await polling;
  assert.match(flatten(env.nodes.get('account-actions')), /one@example.test/);
  assert.equal(env.timers.size, 0);
});

test('pending approval polling ends at expiry and does not restart a terminal or failed flow', async () => {
  for (const terminal of ['denied', 'expired', 'unavailable']) {
    const env = setup(), facts = pendingIdentity(env);
    const initial = env.ui.refresh(true); env.calls.shift().answer(facts); await initial;
    const polling = env.fireTimer();
    env.calls.shift().answer(terminal === 'unavailable' ? {error: 'network_down'} : {status: terminal});
    await settled();
    if (terminal !== 'unavailable') env.calls.shift().answer(facts);
    await polling;
    const refresh = env.ui.refresh(true); env.calls.shift().answer(facts); await refresh;
    assert.equal(env.timers.size, 0, 'a stale status refresh cannot restart a completed/failed flow');
    if (terminal === 'unavailable') {
      const manual = descendants(env.ui.renderDevices()).find(n => n.textContent === '检查绑定结果');
      const retry = manual.onclick(); env.calls.shift().answer({status: 'pending'}); await retry;
      assert.equal(env.timers.size, 1, 'explicit check can resume after a network failure');
    }
  }
  const env = setup(), facts = pendingIdentity(env);
  facts.flow.expires_at = env.now() / 1000 + 4;
  const initial = env.ui.refresh(true); env.calls.shift().answer(facts); await initial;
  const poll = env.fireTimer(); env.calls.shift().answer({status: 'pending'}); await poll;
  assert.equal(env.timers.size, 1);
  await env.fireTimer();
  assert.equal(env.calls.length, 0, 'expiry does not send another poll');
  assert.equal(env.timers.size, 0);
  const reload = env.ui.refresh(true); env.calls.shift().answer(facts); await reload;
  assert.equal(env.timers.size, 0);
});

test('logout cancels pending approval and ignores late approved or failed responses', async () => {
  for (const result of [{status: 'approved'}, {error: 'network_down'}]) {
    const env = setup(), facts = {...pendingIdentity(env), ...person()};
    const initial = env.ui.refresh(true); env.calls.shift().answer(facts); await initial;
    const polling = env.fireTimer(), pollCall = env.calls.shift();
    const exiting = descendants(env.nodes.get('account-actions')).find(n => n.textContent === '退出网页账号').onclick();
    assert.equal(env.calls[0].url, '/api/identity/logout');
    pollCall.answer(result); await polling;
    assert.equal(env.calls.length, 1, 'late approval cannot refresh or relogin the account');
    env.calls.shift().answer({remote_revoked: true}); await settled();
    env.calls.shift().answer({status: 'signed_out', csrf_token: 'csrf'}); await exiting;
    assert.equal(env.timers.size, 0);
    assert.match(env.ui.context(), /anonymous/);
    assert.doesNotMatch(env.nodes.get('notice').textContent, /登录与设备绑定完成|network_down/);
  }
});

test('logout cancels a timer before it sends and cloud pages never poll local identity', async () => {
  const env = setup();
  const initial = env.ui.refresh(true);
  env.calls.shift().answer({...pendingIdentity(env), ...person()}); await initial;
  assert.equal(env.timers.size, 1);
  const exiting = descendants(env.nodes.get('account-actions')).find(n => n.textContent === '退出网页账号').onclick();
  assert.equal(env.timers.size, 0);
  env.calls.shift().answer({remote_revoked: true}); await settled();
  env.calls.shift().answer({status: 'signed_out', csrf_token: 'csrf'}); await exiting;
  assert.equal(env.calls.length, 0);
  const cloud = setup('cloud');
  const loading = cloud.ui.refresh(true);
  cloud.calls.shift().answer({...pendingIdentity(cloud), ...person()}); await loading;
  assert.equal(cloud.timers.size, 0);
});

test('a refreshed identity without a pending flow cancels its scheduled poll', async () => {
  const env = setup();
  const initial = env.ui.refresh(true); env.calls.shift().answer(pendingIdentity(env)); await initial;
  assert.equal(env.timers.size, 1);
  const refresh = env.ui.refresh(true); env.calls.shift().answer({status: 'signed_out', csrf_token: 'csrf'}); await refresh;
  assert.equal(env.timers.size, 0);
});

test('device selection changes request scope and invalidates prior response context', async () => {
  const {ui, nodes, calls} = setup();
  const initial = ui.refresh(true); calls.shift().answer(person()); await initial;
  let reset = false; ui.connect(value => {reset = value;});
  const before = ui.context(), select = nodes.get('device-scope');
  select.value = 'pc'; select.onchange();
  assert.equal(reset, true);
  assert.notEqual(ui.context(), before);
  assert.equal(ui.isLocal(), false);
  assert.equal(ui.api('collections', '?limit=25'), '/api/project/collections?limit=25&device=pc');
});

function pageSetup(view, identity, connectContent = async () => 'connection facts', flow = '', mode = 'cloud', search = undefined, cloudUrl = '') {
  const nodes = new Map();
  const element = () => Object.assign(new Element('div'), {
    textContent: '', attributes: {}, addEventListener() {},
    setAttribute(name, value) { this.attributes[name] = value; },
  });
  const get = key => {
    if (!nodes.has(key)) nodes.set(key, element());
    return nodes.get(key);
  };
  get('connection').textContent = '正在读取状态…';
  let scope = 'owner';
  const refreshes = [];
  const context = vm.createContext({
    document: {body: {dataset: {mode, cloudUrl}}, getElementById: get,
      createElement: element, createDocumentFragment: element, querySelectorAll: () => [], addEventListener() {}},
    Node: Element,
    window: {addEventListener() {}, SpireProject: {}, SpireIdentity: {
      context: () => scope, isLocal: () => false, refresh: async force => { refreshes.push(force); return identity; },
      renderDevices: () => 'account facts', renderConnect: connectContent,
      localOnly() { scope = 'local'; return {status: 'local_only'}; }, connect() {},
    }},
    location: {search: search === undefined ? ('?view=' + view + (flow ? '&flow=' + flow : '')) : search}, history: {}, Date, URLSearchParams,
    setInterval() {},
  });
  vm.runInContext(readFileSync(new URL('../spireagent/console/console.js', import.meta.url), 'utf8'), context);
  return {get, context, refreshes, changeScope: () => {scope = 'other';}};
}
const settled = () => new Promise(resolve => setImmediate(resolve));

test('no-cloud local root selects the local home without identity, cloud reads, or commands', async () => {
  const {get, context, refreshes} = pageSetup('campaigns', null, undefined, '', 'local', '', '');
  await settled();
  assert.equal(vm.runInContext('state.view', context), 'local-home');
  assert.equal(get('title').textContent, '本机工作台');
  assert.equal(get('connection').textContent, '本机工作台');
  assert.deepEqual(refreshes, []);
  const content = get('content');
  const links = descendants(content).filter(item => typeof item.href === 'string');
  assert.deepEqual(links.map(item => item.href), [
    '?view=local-models', '?view=local-workspace', '?view=campaigns',
  ]);
  assert.match(flatten(content), /本机模型与资料查看不需要云端登录/);
  assert.match(flatten(content), /上传另需设备授权、开启上传设置且本机投递服务实际运行/);
  assert.equal(vm.runInContext('views.campaigns[1]', context).includes("后台自动上传"), false);
});

test('local browser still hides login when Hub is not configured', () => {
  const env = setup('local', '', '');
  env.ui.localOnly();
  assert.equal(descendants(env.nodes.get('account-actions')).some(n => n.textContent === '登录项目账号'), false);
});

test('cloud shell cannot select the local-only home through a query string', async () => {
  const {context} = pageSetup('local-home', {status: 'signed_out'}, undefined, '', 'cloud', '?view=local-home');
  assert.equal(vm.runInContext('state.view', context), 'collections');
});

test('local campaign deep links remain available and cloud links appear only when configured', async () => {
  const campaign = pageSetup('campaigns', {status: 'signed_out'}, undefined, '', 'local', '?view=campaigns');
  assert.equal(vm.runInContext('state.view', campaign.context), 'campaigns');

  const configuredHome = pageSetup('local-home', null, undefined, '', 'local', '', 'https://hub.example');
  await settled();
  assert.match(flatten(configuredHome.get('content')), /可从右上角打开云端/);
});

test('account and connection pages finish status from observed identity', async () => {
  for (const view of ['devices', 'connect']) {
    const {get, context} = pageSetup(view, {status: 'signed_in', observed_at: '2026-09-13T11:00:00Z'});
    await settled();
    assert.equal(get('connection').textContent, '已通过身份验证');
    assert.match(get('updated').textContent, /^账号更新于 /);
    assert.doesNotMatch(get('updated').textContent, /未观测|尚未/);
    assert.equal(get('content').attributes['aria-busy'], 'false');
    assert.notEqual(vm.runInContext('renderedContext', context), null);
  }
});

test('account state does not invent authentication or an observation timestamp', async () => {
  for (const [status, label] of [['signed_out', '未登录项目账号'],
    ['reconnect_required', '需要重新登录'], ['unavailable', '账号状态暂不可用']]) {
    const {get} = pageSetup('devices', {status});
    await settled();
    assert.equal(get('connection').textContent, label);
    assert.equal(get('updated').textContent, '账号观测时间未提供');
  }
});

test('connection response cannot repaint a changed identity scope', async () => {
  let finish;
  const pending = new Promise(resolve => {finish = resolve;});
  const {get, changeScope} = pageSetup('connect', {status: 'signed_in'}, () => pending);
  await settled();
  changeScope(); finish('previous account private connection');
  await settled();
  assert.equal(get('content').children.includes('previous account private connection'), false);
  assert.notEqual(get('connection').textContent, '已通过身份验证');
});


test('switching connection flow removes the previous approval panel before its replacement arrives', async () => {
  let finish, calls = 0;
  const pending = new Promise(resolve => {finish = resolve;});
  const {get, context} = pageSetup('connect', {status: 'signed_in'},
    () => ++calls === 1 ? 'approval for first flow' : pending, 'a'.repeat(32));
  await settled();
  assert.deepEqual(get('content').children, ['approval for first flow']);
  context.location.search = '?view=connect&flow=' + 'b'.repeat(32);
  const loading = vm.runInContext('readLocation(); load(true)', context);
  await settled();
  assert.equal(get('content').children.includes('approval for first flow'), false);
  finish('approval for second flow'); await loading;
  assert.deepEqual(get('content').children, ['approval for second flow']);
});


test('system preserves capacity attention and missing observations without claiming backup success', async () => {
  const {context} = pageSetup('devices', {status: 'signed_in'});
  await settled();
  const flatten = element => [element.textContent || '', ...(element.children || []).map(
    child => typeof child === 'string' ? child : flatten(child))].join(' ');
  for (const [status, phrase] of [['attention', '容量不足'], ['unknown', '容量未完整观测']]) {
    context.capacityFixture = {storage: {free_bytes: null, total_bytes: null,
      capacity: {status, free_inodes: null, reserve_bytes: 6442450944, reserve_inodes: 100000}},
      backup: {availability: 'unavailable'}};
    const rendered = flatten(vm.runInContext('system(capacityFixture)', context)).replace(/\s+/g, ' ');
    assert.match(rendered, new RegExp(phrase));
    assert.match(rendered, /可用 \/ 总容量 未观测 \/ 未观测/);
    assert.match(rendered, /备份新鲜度 未观测/);
    assert.match(rendered, /不会自动删除数据或镜像/);
    assert.doesNotMatch(rendered, /运行余量充足|在有效期内/);
  }
});

test('system distinguishes local and cloud sources and never recommends automatic SHA alignment', async () => {
  const {context} = pageSetup('devices', {status: 'signed_in'});
  await settled();
  vm.runInContext('local = true', context);
  context.versionFixture = {identity: {source_revision: 'a'.repeat(40)},
    cloud: {producer: {source_revision: 'b'.repeat(40)}}, cloud_status: 'stale'};
  const flatten = element => [element.textContent || '', ...(element.children || []).map(
    child => typeof child === 'string' ? child : flatten(child))].join(' ');
  const rendered = flatten(vm.runInContext('system(versionFixture)', context));
  assert.match(rendered, /本机工作台来源/);
  assert.match(rendered, /Hub 来源/);
  assert.match(rendered, /来源不同不等于不兼容/);
  assert.match(rendered, /不会随 main 自动安装/);
  assert.match(rendered, /stale/);
  assert.match(rendered, /a{40}/);
  assert.match(rendered, /b{40}/);
});


test('bound member continues into recording setup without the old configuration handoff', async () => {
  const {ui, calls} = setup();
  const initial = ui.refresh(true);
  calls.shift().answer({...person(), hub_configured: true, device_credential_present: true,
    delivery_configured: false});
  await initial;
  const page = ui.renderDevices();
  const flatten = element => [element.textContent || '', ...(element.children || []).map(flatten)].join(' ');
  assert.match(flatten(page), /确认日常录制授权/);
  assert.doesNotMatch(flatten(page), /领取活动配置/);
  assert.equal(page.children.find(item => item.textContent === '打开真人采集 →')?.href,
    '?view=campaigns');
});

test('record list and detail use the verified activity association without guessing from campaign IDs', async () => {
  const {context} = pageSetup('devices', {status: 'signed_in'});
  await settled();
  const flatten = element => [element.textContent || '', ...(element.children || []).map(
    child => typeof child === 'string' ? child : flatten(child))].join(' ');
  for (const [association, expected] of [
    [{kind: 'default', name: '每日练习'}, '日常录制 · 每日练习'],
    [{kind: 'activity', name: '专题一'}, '专题活动 · 专题一'],
    [{kind: 'unlinked', name: null}, '未关联专题活动'],
    [undefined, '录制用途尚未关联'],
  ]) {
    context.recordFixture = {id: 'a'.repeat(32), campaign_id: 'campaign-looking-like-a-default',
      collection_context: association};
    for (const expression of ['collectionTable([recordFixture])', 'detail({item: recordFixture})']) {
      const rendered = flatten(vm.runInContext(expression, context));
      assert.ok(rendered.includes(expected), expression + ' must preserve association status');
    }
  }
});

test('local list distinguishes an unqueried cloud association from a missing enrollment', async () => {
  const {context} = pageSetup('devices', {status: 'signed_in'});
  await settled();
  context.localRecordFixture = {local_delivery: true, campaign_id: 'campaign-looking-like-a-default'};
  assert.equal(vm.runInContext('collectionContext(localRecordFixture)', context),
    '本机记录 · 云端归属见详情');
  context.localRecordFixture.collection_context = {kind: 'default', name: '日常真人采集'};
  assert.equal(vm.runInContext('collectionContext(localRecordFixture)', context),
    '日常录制 · 日常真人采集');
});

test('overview still presents recording setup when this computer has no delivery configuration', async () => {
  const {context, get} = pageSetup('devices', {status: 'signed_in'});
  await settled();
  const rendered = [];
  context.window.SpireProject.render = async (view, identity) => {
    rendered.push([view, identity.status]);
    return 'durable setup steps';
  };
  context.window.SpireIdentity.api = route => '/app/api/' + route;
  context.fetch = async () => ({ok: true, json: async () => ({status: 'not_configured'})});
  context.AbortSignal = AbortSignal;
  context.location.search = '?view=overview';
  await vm.runInContext('readLocation(); load(true)', context);
  assert.deepEqual(rendered, [['collection-overview', 'signed_in']]);
  assert.equal(get('content').children[0].children[0], 'durable setup steps');
});


test('ordinary project reload reuses identity cache but explicit refresh still forces verification', async () => {
  const {context,refreshes}=pageSetup('devices',{status:'signed_in'});
  await settled();
  await context.window.SpireProject.reload();
  assert.equal(refreshes.at(-1),false);
  await vm.runInContext('load(true)',context);
  assert.equal(refreshes.at(-1),true);
});
