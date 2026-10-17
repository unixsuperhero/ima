local api = vim.api
local project = vim.fn.getcwd()
vim.opt.runtimepath:prepend(project)
local command = { 'uv', 'run', '--project', project, 'now' }
local child = vim.env.NOW_NVIM_CHILD
if not child then vim.env.NOW_HOME = vim.fn.tempname() end
vim.fn.mkdir(vim.env.NOW_HOME, 'p')
local now = require('now')
now.setup({ command = command })

local function eq(expected, actual)
  assert(vim.deep_equal(expected, actual), 'expected ' .. vim.inspect(expected) .. ', got ' .. vim.inspect(actual))
end
local function wait_for(predicate)
  assert(vim.wait(10000, predicate, 10), 'timed out')
end
local function lines()
  return api.nvim_buf_get_lines(0, 0, -1, false)
end
local function keys(value)
  api.nvim_feedkeys(api.nvim_replace_termcodes(value, true, false, true), 'xt', false)
end
local function set(value, row, col)
  vim.bo.undolevels = -1
  api.nvim_buf_set_lines(0, 0, -1, false, value)
  vim.bo.undolevels = 1000
  api.nvim_win_set_cursor(0, { row or 1, col or 0 })
end
local function cli(args, payload)
  local argv = vim.list_extend(vim.deepcopy(command), args)
  local result = vim.system(argv, { text = true, stdin = payload and vim.json.encode(payload) }):wait()
  assert(result.code == 0, result.stderr)
  return vim.json.decode(result.stdout)
end
local function durable()
  local path = vim.env.NOW_HOME .. '/draft.json'
  local ok, value = pcall(function() return vim.json.decode(table.concat(vim.fn.readfile(path), '\n')) end)
  return ok and value or {}
end
local function save(expected)
  now.save()
  wait_for(function() return durable().text == expected and not vim.bo.modified end)
end
local function run_child(mode)
  local result = vim.system({ vim.v.progpath, '--headless', '-u', 'NONE', '-l', project .. '/tests/neovim_draft.lua' },
    { text = true, env = { NOW_NVIM_CHILD = mode } }):wait(20000)
  assert(result.code == 0, result.stderr .. result.stdout)
end
local function tasks()
  local code = 'import json; from dataclasses import asdict; from now.store import Store; print(json.dumps([asdict(t) for t in Store().tasks().values()]))'
  local result = vim.system({ 'uv', 'run', '--project', project, 'python', '-c', code }, { text = true }):wait()
  assert(result.code == 0, result.stderr)
  return vim.json.decode(result.stdout)
end

local function run()
  if child == 'restart' then vim.cmd('cd /tmp') end
  local original_buf, original_win = api.nvim_get_current_buf(), api.nvim_get_current_win()
  api.nvim_buf_set_lines(original_buf, 0, -1, false, { 'unsaved original edits' })
  local original_windows = #api.nvim_list_wins()
  now.open()
  wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
  eq(original_windows + 1, #api.nvim_list_wins())
  eq(original_buf, api.nvim_win_get_buf(original_win))
  eq({ 'unsaved original edits' }, api.nvim_buf_get_lines(original_buf, 0, -1, false))
  eq(true, vim.bo[original_buf].modified)
  vim.cmd.stopinsert()
  if child == 'restart' then
    eq({ 'restart survives', '  child without marker' }, lines())
    eq(project, vim.b.now_cwd)
    return
  elseif child == 'failed-exit' then
    set({ 'must survive failed save and forced exit' })
    now.setup({ command = { '/nonexistent-now-command' } })
    now.save()
    wait_for(function() return #vim.fn.glob(vim.env.NOW_HOME .. '/nvim-recovery-*.json', false, true) > 0 end)
    return
  elseif child == 'hidden-exit' then
    set({ 'hidden draft survives editor exit' })
    vim.cmd('hide enew')
    return
  elseif child == 'recover' then
    eq({ 'must survive failed save and forced exit' }, lines())
    save('must survive failed save and forced exit')
    return
  end

  set({ 'alpha beta' }, 1, 6)
  keys('i<CR><Esc>')
  eq({ '- [ ] alpha ', 'beta' }, lines())
  keys('u')
  eq({ 'alpha beta' }, lines())
  keys('<C-r>')
  eq({ '- [ ] alpha ', 'beta' }, lines())

  set({ 'first' })
  keys('A<CR>second<Esc>')
  eq({ '- [ ] first', 'second' }, lines())
  keys('u')
  eq({ '- [ ] first', '' }, lines())
  keys('u')
  eq({ 'first' }, lines())

  set({ '  - [ ] task' }, 1, 11)
  keys('A<CR><Esc>')
  eq({ '  - [ ] task', '  ' }, lines())
  keys('A<CR><Esc>')
  eq({ '  - [ ] task', '' }, lines())
  keys('A<CR><Esc>')
  eq({ '  - [ ] task', '', '' }, lines())

  set({ '- [ ] parent', '  - [ ] a', '    - [ ] grandchild', '  - [ ] b', '- [ ] next' }, 2)
  keys('<S-Tab>')
  eq({ '- [ ] parent', '  - [ ] b', '- [ ] a', '  - [ ] grandchild', '- [ ] next' }, lines())
  eq(3, api.nvim_win_get_cursor(0)[1])
  keys('u')
  eq({ '- [ ] parent', '  - [ ] a', '    - [ ] grandchild', '  - [ ] b', '- [ ] next' }, lines())
  keys('<C-r>')
  api.nvim_win_set_cursor(0, { 3, 0 })
  keys('<Tab>')
  eq({ '- [ ] parent', '  - [ ] b', '  - [ ] a', '    - [ ] grandchild', '- [ ] next' }, lines())

  set({ 'first', '  child', 'second', '  nested' })
  keys('<Tab>')
  keys('<S-Tab>')
  eq({ 'first', '  child', 'second', '  nested' }, lines())
  api.nvim_win_set_cursor(0, { 3, 0 })
  keys('<Tab>')
  eq({ 'first', '  child', '  second', '    nested' }, lines())
  keys('u')
  eq({ 'first', '  child', 'second', '  nested' }, lines())

  local buf = api.nvim_get_current_buf()
  local draft_win = api.nvim_get_current_win()
  api.nvim_win_set_cursor(0, { 2, 1 })
  api.nvim_set_current_win(original_win)
  now.open()
  eq(draft_win, api.nvim_get_current_win())
  eq(original_windows + 1, #api.nvim_list_wins())
  eq({ 2, 1 }, api.nvim_win_get_cursor(0))
  api.nvim_win_set_cursor(0, { 3, 2 })
  vim.cmd.close()
  eq(original_win, api.nvim_get_current_win())
  eq(original_windows, #api.nvim_list_wins())
  now.open()
  eq(buf, api.nvim_get_current_buf())
  eq({ 3, 2 }, api.nvim_win_get_cursor(0))
  eq(original_windows + 1, #api.nvim_list_wins())
  eq(original_buf, api.nvim_win_get_buf(original_win))
  eq({ 'unsaved original edits' }, api.nvim_buf_get_lines(original_buf, 0, -1, false))
  keys('<C-r>')
  eq({ 'first', '  child', '  second', '    nested' }, lines())

  set({ 'restart survives', '  child without marker' })
  save('restart survives\n  child without marker')
  run_child('restart')
  vim.cmd('bdelete!')
  now.open()
  wait_for(function() return api.nvim_buf_get_name(0) == 'now://draft' end)
  vim.cmd.stopinsert()
  eq({ 'restart survives', '  child without marker' }, lines())

  set({ 'root', '      invalid jump' })
  now.apply()
  wait_for(function() return vim.bo.modifiable end)
  eq({ 'root', '      invalid jump' }, lines())
  eq({}, tasks())

  set({ '- [ ] parent', '  ? learn it', '  child', 'last unformatted line' })
  now.apply()
  eq(false, vim.bo.modifiable)
  wait_for(function() return vim.bo.modifiable and lines()[1] == '' end)
  local result = tasks()
  eq({ 'parent', 'learn it', 'child', 'last unformatted line' }, vim.tbl_map(function(t) return t.title end, result))
  eq(result[1].id, result[2].parent)
  eq(result[1].id, result[3].parent)
  eq('know', result[2].kind)
  for _, task in ipairs(result) do eq(project, task.cwd) end

  -- Real concurrent write: a stale editor cannot replace the other draft.
  local original = cli({ 'draft', 'read', '--cwd', project })
  cli({ 'draft', 'save' }, { draft_id = original.draft_id, revision = original.revision, text = 'other editor' })
  set({ 'local conflicting text' })
  now.save()
  wait_for(function()
    for _, path in ipairs(vim.fn.glob(vim.env.NOW_HOME .. '/nvim-recovery-*.json', false, true)) do
      local value = vim.json.decode(table.concat(vim.fn.readfile(path), '\n'))
      if value.text == 'local conflicting text' then return true end
    end
    return false
  end)
  eq('other editor', durable().text)
  eq({ 'local conflicting text' }, lines())

  -- Separate real processes exercise exit flush and recovery without live owners.
  local previous_home = vim.env.NOW_HOME
  vim.env.NOW_HOME = vim.fn.tempname()
  vim.fn.mkdir(vim.env.NOW_HOME, 'p')
  run_child('failed-exit')
  run_child('recover')
  eq('must survive failed save and forced exit', durable().text)
  run_child('hidden-exit')
  eq('hidden draft survives editor exit', durable().text)
  vim.fn.delete(vim.env.NOW_HOME, 'rf')
  vim.env.NOW_HOME = previous_home
  print('Neovim draft editing, undo, restart, apply, conflict, and failed-exit recovery passed')
end

local ok, err = xpcall(run, debug.traceback)
if not ok then
  io.stderr:write(err .. '\n')
  vim.cmd('cquit 1')
else
  vim.cmd('qa!')
end
