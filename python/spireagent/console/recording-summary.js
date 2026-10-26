(() => {
  const status = document.getElementById('maintenance-status');
  const stale = document.getElementById('maintenance-stale');
  const duration = document.getElementById('maintenance-duration');
  const minutes = seconds => seconds < 60 ? Math.floor(seconds) + ' 秒' :
    Math.floor(seconds / 60) + ' 分钟';
  const age = value => {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? Math.max(0, (Date.now() - parsed) / 1000) : null;
  };
  const update = () => {
    const since = age(status.dataset.since);
    const checked = age(status.dataset.checkedAt);
    duration.textContent = since === null ? '未记录' : minutes(since);
    const busy = status.dataset.busy === 'true';
    const old = checked === null || checked > (busy ? 600 : 90);
    const slow = busy && since !== null && since > 600;
    stale.hidden = !old && !slow;
    stale.textContent = old ? '无法确认自动入库仍在工作：状态' +
      (checked === null ? '更新时间未记录' : '已 ' + minutes(checked) + ' 未更新') +
      '。这可能是旧页面或工作台已断开；请检查原工作台，不要重复导入。' :
      slow ? '当前步骤已持续 ' + minutes(since) +
      '，尚未完成，可能卡住。请维护者核对原操作；本提示不会终止或重试它。' : '';
  };
  update();
  setInterval(update, 1000);
})();
