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
  if mode == 'custom-bindings' then
    now.setup({ command = command, mappings = { i = { submit = '<C-j>', indent = false, outdent = false },
      n = { indent = '>', outdent = '<' } } })
    now.open()
    wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
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
    now.setup({ command = command })
    now.open()
    wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
    eq(false, vim.bo.modifiable)
    eq({ 'applied exactly once', '  child exactly once' }, lines())
    now.apply()
    wait_for(function() return vim.bo.modifiable and lines()[1] == '' end)
    local code = 'import json; from now.store import Store; print(json.dumps([t.title for t in Store().tasks().values()]))'
    local result = vim.system({ 'uv', 'run', '--project', project, 'python', '-c', code }, { text = true }):wait()
    assert(result.code == 0, result.stderr)
    eq({ 'applied exactly once', 'child exactly once' }, vim.json.decode(result.stdout))
    return
  end

  local home = vim.env.NOW_HOME
  for _, case in ipairs({ 'custom-bindings', 'disabled-bindings' }) do
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
  print('Neovim configurable bindings, asynchronous coalescing, and receipt restart recovery passed')
end
local ok, err = xpcall(run, debug.traceback)
if not ok then io.stderr:write(err .. '\n'); vim.cmd('cquit 1') else vim.cmd('qa!') end
