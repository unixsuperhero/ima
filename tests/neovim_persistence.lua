local api = vim.api
local project = vim.fn.getcwd()
vim.opt.runtimepath:prepend(project)
local mode = vim.env.NOW_NVIM_CASE
if not mode then vim.env.NOW_HOME = vim.fn.tempname() end
vim.fn.mkdir(vim.env.NOW_HOME, 'p')
local now = require('now')
local command = { 'uv', 'run', '--project', project, 'now' }
local function eq(expected, actual)
  assert(vim.deep_equal(expected, actual), 'expected ' .. vim.inspect(expected) .. ', got ' .. vim.inspect(actual))
end
local function wait_for(predicate)
  assert(vim.wait(10000, predicate, 10), 'timed out')
end
local function lines() return api.nvim_buf_get_lines(0, 0, -1, false) end
local function keys(value)
  api.nvim_feedkeys(api.nvim_replace_termcodes(value, true, false, true), 'xt', false)
end
local function durable()
  local ok, value = pcall(function()
    return vim.json.decode(table.concat(vim.fn.readfile(vim.env.NOW_HOME .. '/draft.json'), '\n'))
  end)
  return ok and value or {}
end
local function subprocess(case)
  local result = vim.system({ vim.v.progpath, '--headless', '-u', 'NONE', '-l', project .. '/tests/neovim_persistence.lua' },
    { text = true, env = { NOW_NVIM_CASE = case } }):wait(20000)
  assert(result.code == 0, result.stderr .. result.stdout)
end
local function run()
  if mode == 'checkpoint-failure' then
    now.setup({ command = command })
    now.open()
    wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
    vim.cmd.stopinsert()
    api.nvim_buf_set_lines(0, 0, -1, false, { 'checkpoint must precede apply' })
    now.save()
    wait_for(function() return durable().text == 'checkpoint must precede apply' and not vim.bo.modified end)
    local before = durable()
    local fs_open, notify = vim.uv.fs_open, vim.notify
    local errors = {}
    vim.notify = function(message) errors[#errors + 1] = message end
    vim.uv.fs_open = function(path, flags, permissions, callback)
      if path:match('%.sync%.tmp$') then return nil, 'injected recovery write failure' end
      return fs_open(path, flags, permissions, callback)
    end
    now.apply()
    vim.uv.fs_open, vim.notify = fs_open, notify
    eq(true, vim.bo.modifiable)
    eq(before, durable())
    assert(table.concat(errors, '\n'):find('injected recovery write failure', 1, true))
    local result = vim.system({ 'uv', 'run', '--project', project, 'python', '-c',
      'import json; from now.store import Store; print(json.dumps([t.title for t in Store().tasks().values()]))' }, { text = true }):wait()
    assert(result.code == 0, result.stderr)
    eq({}, vim.json.decode(result.stdout))
    api.nvim_buf_set_lines(0, 0, -1, false, { 'editable after checkpoint failure' })
    now.apply()
    wait_for(function() return vim.bo.modifiable and lines()[1] == '' end)
    return
  elseif mode == 'lost-save-response' or mode == 'unrelated-revision' then
    local code = [=[import json, os, pathlib, subprocess, sys, time
payload = sys.stdin.read()
marker = pathlib.Path(os.environ['NOW_HOME']) / 'dropped-save'
drop = sys.argv[1:] == ['draft', 'save'] and not marker.exists()
if drop: time.sleep(0.2)
result = subprocess.run(['now', *sys.argv[1:]], input=payload, text=True, capture_output=True)
if drop and result.returncode == 0:
    marker.touch()
    if os.environ['NOW_NVIM_CASE'] == 'unrelated-revision':
        saved = json.loads(result.stdout)
        for text in ['unrelated edit', saved['text']]:
            saved['text'] = text
            result = subprocess.run(['now', 'draft', 'save'], input=json.dumps(saved), text=True, capture_output=True, check=True)
            saved = json.loads(result.stdout)
    print('injected lost save response', file=sys.stderr)
    sys.exit(1)
print(result.stdout, end='')
print(result.stderr, end='', file=sys.stderr)
sys.exit(result.returncode)
]=]
    local errors, notify = {}, vim.notify
    vim.notify = function(message) errors[#errors + 1] = message end
    now.setup({ command = { 'uv', 'run', '--project', project, 'python', '-c', code } })
    now.open()
    wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
    vim.cmd.stopinsert()
    api.nvim_buf_set_lines(0, 0, -1, false, { 'saved snapshot' })
    now.save()
    api.nvim_buf_set_lines(0, 0, -1, false, { 'queued newer text' })
    if mode == 'lost-save-response' then
      wait_for(function() return durable().text == 'queued newer text' and not vim.bo.modified end)
      eq(2, durable().revision)
    else
      wait_for(function() return table.concat(errors, '\n'):find('local text retained', 1, true) end)
      eq('saved snapshot', durable().text)
      eq(3, durable().revision)
      eq({ 'queued newer text' }, lines())
      eq(true, vim.bo.modified)
    end
    vim.notify = notify
    return
  elseif mode == 'receipt-live-peer' then
    now.setup({ command = command })
    now.open()
    wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
    vim.cmd.stopinsert()
    eq(true, vim.bo.modifiable)
    eq({ '' }, lines())
    return
  end
  if mode == 'custom-bindings' then
    now.setup({ command = command, mappings = { i = { submit = '<C-j>', indent = false, outdent = false },
      n = { indent = '>', outdent = '<' } } })
    now.open()
    wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
    vim.cmd.stopinsert()
    keys('iword<CR>next<C-j><Esc>')
    eq({ 'word', '- [ ] next', '' }, lines())
    api.nvim_win_set_cursor(0, { 2, 0 })
    keys('>')
    eq({ 'word', '  - [ ] next', '  ' }, lines())
    return
  elseif mode == 'disabled-bindings' then
    now.setup({ command = command, mappings = false })
    now.open()
    wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
    vim.cmd.stopinsert()
    keys('iword<CR>next<Esc>')
    eq({ 'word', 'next' }, lines())
    return
  elseif mode == 'lost-response' then
    -- Drop one response after the real backend commits, without substituting storage.
    local code = [=[import os, pathlib, subprocess, sys
result = subprocess.run(['now', *sys.argv[1:]], input=sys.stdin.read(), text=True, capture_output=True)
marker = pathlib.Path(os.environ['NOW_HOME']) / 'dropped-response'
if sys.argv[1:] == ['draft', 'apply'] and not marker.exists() and result.returncode == 0:
    marker.touch()
    print('injected transport failure after committed apply', file=sys.stderr)
    sys.exit(1)
print(result.stdout, end='')
print(result.stderr, end='', file=sys.stderr)
sys.exit(result.returncode)
]=]
    now.setup({ command = { 'uv', 'run', '--project', project, 'python', '-c', code } })
    now.open()
    wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
    vim.cmd.stopinsert()
    api.nvim_buf_set_lines(0, 0, -1, false, { 'applied exactly once', '  child exactly once' })
    now.apply()
    wait_for(function()
      for _, path in ipairs(vim.fn.glob(vim.env.NOW_HOME .. '/nvim-recovery-*.json', false, true)) do
        local value = vim.json.decode(table.concat(vim.fn.readfile(path), '\n'))
        if value.pending_apply and durable().text == '' then return true end
      end
      return false
    end)
    eq(false, vim.bo.modifiable)
    eq({ 'applied exactly once', '  child exactly once' }, lines())
    return
  elseif mode == 'receipt-restart' then
    local abandoned = vim.fn.glob(vim.env.NOW_HOME .. '/nvim-recovery-*.json', false, true)
    eq(1, #abandoned)
    now.setup({ command = command })
    now.open()
    wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
    vim.cmd.stopinsert()
    eq(false, vim.bo.modifiable)
    eq({ 'applied exactly once', '  child exactly once' }, lines())
    eq(0, vim.fn.filereadable(abandoned[1]))
    local claimed = vim.fn.glob(vim.env.NOW_HOME .. '/nvim-recovery-*.json', false, true)
    eq(1, #claimed)
    eq(vim.uv.os_getpid(), tonumber(claimed[1]:match('nvim%-recovery%-(%d+)%-')))
    subprocess('receipt-live-peer')
    eq(1, vim.fn.filereadable(claimed[1]))
    now.apply()
    wait_for(function() return vim.bo.modifiable and lines()[1] == '' end)
    local code = 'import json; from now.store import Store; print(json.dumps([t.title for t in Store().tasks().values()]))'
    local result = vim.system({ 'uv', 'run', '--project', project, 'python', '-c', code }, { text = true }):wait()
    assert(result.code == 0, result.stderr)
    eq({ 'applied exactly once', 'child exactly once' }, vim.json.decode(result.stdout))
    return
  end

  local home = vim.env.NOW_HOME
  local ui_home = home .. '/initial-insert'
  vim.fn.mkdir(ui_home, 'p')
  local channel = vim.fn.jobstart({ vim.v.progpath, '--headless', '--embed', '-u', 'NONE' },
    { rpc = true, env = { NOW_HOME = ui_home } })
  assert(channel > 0)
  local function remote(code, args) return vim.rpcrequest(channel, 'nvim_exec_lua', code, args or {}) end
  remote("local project, command = ...; vim.opt.runtimepath:prepend(project); require('now').setup({command=command}); vim.cmd.Now()", { project, command })
  wait_for(function() return remote("return vim.api.nvim_get_mode().mode") == 'i' end)
  vim.rpcrequest(channel, 'nvim_input', 'initial insert works<Esc>')
  wait_for(function() return remote("return vim.api.nvim_get_current_line()") == 'initial insert works' end)
  eq(2, remote('return #vim.api.nvim_list_wins()'))
  vim.fn.jobstop(channel)
  for _, case in ipairs({ 'custom-bindings', 'disabled-bindings', 'checkpoint-failure', 'lost-save-response', 'unrelated-revision' }) do
    vim.env.NOW_HOME = home .. '/' .. case
    vim.fn.mkdir(vim.env.NOW_HOME, 'p')
    subprocess(case)
  end
  vim.env.NOW_HOME = home .. '/receipt'
  vim.fn.mkdir(vim.env.NOW_HOME, 'p')
  subprocess('lost-response')
  subprocess('receipt-restart')
  vim.env.NOW_HOME = home .. '/autosave'
  vim.fn.mkdir(vim.env.NOW_HOME, 'p')

  -- Delay real saves, then type while one is in flight. No backend stub.
  local code = [=[import os, sys, time
if sys.argv[1:] == ['draft', 'save']: time.sleep(0.4)
os.execvp('now', ['now', *sys.argv[1:]])
]=]
  now.setup({ command = { 'uv', 'run', '--project', project, 'python', '-c', code } })
  now.open()
  wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
  vim.cmd.stopinsert()
  keys('ifirst snapshot<Esc>')
  now.save()
  keys('A plus newest text<Esc>')
  local position = api.nvim_win_get_cursor(0)
  local undo = vim.fn.undotree().seq_cur
  local buf = api.nvim_get_current_buf()
  wait_for(function() return durable().text == 'first snapshot plus newest text' and not vim.bo.modified end)
  eq(buf, api.nvim_get_current_buf())
  eq(position, api.nvim_win_get_cursor(0))
  eq(undo, vim.fn.undotree().seq_cur)
  eq({ 'first snapshot plus newest text' }, lines())

  keys('A autosaved by event<Esc>')
  api.nvim_exec_autocmds('TextChanged', { buffer = buf })
  wait_for(function() return durable().text == 'first snapshot plus newest text autosaved by event' end)
  vim.cmd('hide enew')
  now.open()
  eq(buf, api.nvim_get_current_buf())
  eq({ 'first snapshot plus newest text autosaved by event' }, lines())
  local saved_while_typing = false
  for i = 1, 50 do
    api.nvim_buf_set_lines(buf, 0, -1, false, { 'continuous typing ' .. i })
    api.nvim_exec_autocmds('TextChangedI', { buffer = buf })
    vim.wait(30, function() return false end, 5)
    if (durable().text or ''):match('^continuous typing %d+$') then saved_while_typing = true end
  end
  eq(true, saved_while_typing)
  wait_for(function() return durable().text == 'continuous typing 50' and not vim.bo.modified end)
  print('Neovim configurable bindings, asynchronous coalescing, and receipt restart recovery passed')
end
local ok, err = xpcall(run, debug.traceback)
if not ok then io.stderr:write(err .. '\n'); vim.cmd('cquit 1') else vim.cmd('qa!') end
