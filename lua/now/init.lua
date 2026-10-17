local M = {}
local api, uv = vim.api, vim.uv
local defaults = {
  indent_size = 2,
  command = { 'now' },
  mappings = {
    i = { submit = '<CR>', indent = '<Tab>', outdent = '<S-Tab>' },
    n = { indent = '<Tab>', outdent = '<S-Tab>' },
  },
}
local config = vim.deepcopy(defaults)
local state
local opening = false

local function notify(message)
  vim.notify('Now: ' .. message, vim.log.levels.ERROR)
end

local function root()
  local home, xdg = vim.env.NOW_HOME, vim.env.XDG_DATA_HOME
  if home and home ~= '' then return home end
  return ((xdg and xdg ~= '' and xdg) or (vim.fn.expand('~') .. '/.local/share')) .. '/now'
end

local function request(action, payload, callback)
  local argv = vim.list_extend(vim.deepcopy(config.command), { 'draft', action })
  if action == 'read' then
    vim.list_extend(argv, { '--cwd', payload })
  end
  local ok, err = pcall(vim.system, argv, {
    text = true,
    stdin = action ~= 'read' and vim.json.encode(payload) or nil,
  }, function(result)
    vim.schedule(function()
      if result.code ~= 0 then
        callback(nil, vim.trim(result.stderr or '') ~= '' and vim.trim(result.stderr) or ('command exited ' .. result.code))
        return
      end
      local decoded, value = pcall(vim.json.decode, result.stdout)
      if not decoded or type(value) ~= 'table' then
        callback(nil, 'invalid JSON response from now draft ' .. action)
        return
      end
      callback(value)
    end)
  end)
  if not ok then
    vim.schedule(function() callback(nil, tostring(err)) end)
  end
end

local function text(s)
  if api.nvim_buf_is_loaded(s.buf) then
    s.text = table.concat(api.nvim_buf_get_lines(s.buf, 0, -1, false), '\n')
  end
  return s.text
end

local function recovery_data(s)
  return vim.json.encode({ draft = s.draft, text = text(s), pending_apply = s.pending_apply })
end

local function recovery_path(s)
  if not s.recovery then
    s.recovery = root() .. '/nvim-recovery-' .. uv.os_getpid() .. '-' .. uv.hrtime() .. '.json'
  end
  return s.recovery
end

-- Only lifecycle flushes write synchronously. Failed background saves use libuv.
local function recover(s, synchronous)
  local path = recovery_path(s)
  local data = recovery_data(s)
  local directory_path = root()
  local made, mkdir_err = pcall(vim.fn.mkdir, directory_path, 'p', 448)
  if not made then
    notify('cannot preserve recovery file ' .. path .. ': ' .. tostring(mkdir_err))
    return false
  end
  if synchronous then
    if s.recovery_busy then s.recovery_next = data end
    local ok, err = pcall(function()
      local fd = assert(uv.fs_open(path .. '.sync.tmp', 'w', 384))
      local written, write_err = uv.fs_write(fd, data, 0)
      local synced, sync_err = uv.fs_fsync(fd)
      uv.fs_close(fd)
      assert(written == #data, write_err or 'incomplete recovery write')
      assert(synced, sync_err)
      assert(uv.fs_rename(path .. '.sync.tmp', path))
      local directory = assert(uv.fs_open(directory_path, 'r', 0))
      local persisted, persist_err = uv.fs_fsync(directory)
      uv.fs_close(directory)
      assert(persisted, persist_err)
    end)
    if not ok then notify('cannot preserve recovery file ' .. path .. ': ' .. tostring(err)) end
    return ok
  end
  s.recovery_next = data
  if s.recovery_busy then return end
  local function write_next()
    local snapshot = s.recovery_next
    s.recovery_next = nil
    s.recovery_busy = true
    uv.fs_open(path .. '.tmp', 'w', 384, function(open_err, fd)
      local function finish(err)
        vim.schedule(function()
          s.recovery_busy = false
          if err then notify('cannot preserve recovery file ' .. path .. ': ' .. tostring(err)) end
          if s.recovery_next then write_next() end
        end)
      end
      if open_err then finish(open_err); return end
      uv.fs_write(fd, snapshot, 0, function(write_err, written)
        uv.fs_fsync(fd, function(sync_err)
          uv.fs_close(fd, function(close_err)
            local err = write_err or sync_err or close_err
            if not err and written ~= #snapshot then err = 'incomplete recovery write' end
            if err then finish(err); return end
            uv.fs_rename(path .. '.tmp', path, function(rename_err)
              if rename_err then finish(rename_err); return end
              uv.fs_open(directory_path, 'r', 0, function(dir_err, directory)
                if dir_err then finish(dir_err); return end
                uv.fs_fsync(directory, function(persist_err)
                  uv.fs_close(directory, function(dir_close_err) finish(persist_err or dir_close_err) end)
                end)
              end)
            end)
          end)
        end)
      end)
    end)
  end
  write_next()
end

local function remove_recovery(s)
  if s.recovery_busy then
    vim.defer_fn(function() remove_recovery(s) end, 20)
  elseif s.recovery and not s.pending_apply and text(s) == s.draft.text then
    uv.fs_unlink(s.recovery)
    s.recovery = nil
  end
end

local function modifiable(s, value)
  if api.nvim_buf_is_valid(s.buf) then vim.bo[s.buf].modifiable = value end
end

local pump
local function save_failed(s, err)
  s.busy = false
  s.applying = false
  modifiable(s, true)
  recover(s, false)
  notify(err .. '; local text retained at ' .. recovery_path(s))
end

local function apply_saved(s)
  if s.recovery_busy then
    vim.defer_fn(function() apply_saved(s) end, 20)
    return
  end
  local previous_pending = s.pending_apply
  s.pending_apply = s.pending_apply or {
    draft_id = s.draft.draft_id, revision = s.draft.revision, indent_size = config.indent_size,
  }
  if not recover(s, true) then
    s.pending_apply = previous_pending
    s.busy = false
    s.applying = false
    modifiable(s, not s.pending_apply)
    return
  end
  s.busy = true
  request('apply', s.pending_apply, function(result, err)
    if not result then
      -- A read of the same revision proves the completed process did not commit.
      -- Otherwise retain the receipt and keep editing frozen until explicit retry.
      request('read', s.draft.cwd, function(current)
        s.busy = false
        s.applying = false
        if current and current.draft_id == s.pending_apply.draft_id
          and current.revision == s.pending_apply.revision then
          s.pending_apply = nil
          modifiable(s, true)
        end
        recover(s, false)
        notify(err .. '; draft retained. Use :NowApply to retry.')
      end)
      return
    end
    request('read', s.draft.cwd, function(next_draft, read_err)
      s.busy = false
      s.applying = false
      if not next_draft then
        recover(s, false)
        notify(read_err .. '; apply confirmed, retry :NowApply to recover the next draft.')
        return
      end
      s.pending_apply = nil
      s.draft = next_draft
      s.text = next_draft.text
      if api.nvim_buf_is_valid(s.buf) then
        modifiable(s, true)
        api.nvim_buf_set_lines(s.buf, 0, -1, false, vim.split(next_draft.text, '\n', { plain = true }))
        vim.bo[s.buf].modified = false
        vim.b[s.buf].now_cwd = next_draft.cwd
      end
      remove_recovery(s)
      vim.notify('Now: applied ' .. #result.task_ids .. ' tasks', vim.log.levels.INFO)
    end)
  end)
end

pump = function(s)
  if s.busy or s.pending_apply then return end
  local snapshot = text(s)
  if snapshot == s.draft.text then
    if api.nvim_buf_is_valid(s.buf) then vim.bo[s.buf].modified = false end
    remove_recovery(s)
    if s.applying then apply_saved(s) end
    return
  end
  s.busy = true
  local expected_revision = s.draft.revision + 1
  request('save', { draft_id = s.draft.draft_id, revision = s.draft.revision, text = snapshot }, function(saved, err)
    if not saved then
      recover(s, false)
      request('read', s.draft.cwd, function(current)
        if current and current.draft_id == s.draft.draft_id and current.text == snapshot
          and current.revision == expected_revision then
          s.busy = false
          s.draft = current
          pump(s)
        else
          save_failed(s, err)
        end
      end)
      return
    end
    s.busy = false
    s.draft = saved
    pump(s)
  end)
end

function M.save()
  if not state then return end
  if state.pending_apply then
    recover(state, false)
    return
  end
  pump(state)
end

local function changed(s)
  text(s)
  if not s.applying then
    if s.pending_apply then recover(s, false) else pump(s) end
  end
end

local function flush(s, exiting)
  text(s)
  if text(s) ~= s.draft.text or s.pending_apply then recover(s, true) end
  if not s.pending_apply then pump(s) end
  if exiting then
    vim.wait(3000, function() return not s.busy and not s.recovery_busy end, 10)
    if text(s) ~= s.draft.text or s.pending_apply then recover(s, true) end
  end
end

local function install_mappings(buf)
  if config.mappings == false then return end
  for mode, mappings in pairs(config.mappings) do
    if mappings ~= false then
      for action, key in pairs(mappings) do
        if key ~= false then
          local boundary = mode == 'i' and '<C-G>u' or ''
          local rhs = boundary .. '<Cmd>lua require("now").' .. action .. '()<CR>' .. boundary
          vim.keymap.set(mode, key, rhs, { buffer = buf, silent = true, desc = 'Now ' .. action })
        end
      end
    end
  end
end

function M.open()
  if state and api.nvim_buf_is_loaded(state.buf) then
    local windows = vim.fn.win_findbuf(state.buf)
    if #windows > 0 then
      api.nvim_set_current_win(windows[1])
      return state.buf
    end
    vim.cmd.split()
    api.nvim_win_set_buf(0, state.buf)
    if state.cursor then pcall(api.nvim_win_set_cursor, 0, state.cursor) end
    return state.buf
  end
  if opening then return end
  if state and api.nvim_buf_is_valid(state.buf) then api.nvim_buf_set_name(state.buf, '') end
  opening = true
  request('read', vim.fn.getcwd(), function(draft, err)
    opening = false
    if not draft then notify(err); return end
    local recovered, path
    for _, candidate in ipairs(vim.fn.glob(root() .. '/nvim-recovery-*.json', false, true)) do
      local ok, value = pcall(function() return vim.json.decode(table.concat(vim.fn.readfile(candidate), '\n')) end)
      local owner = tonumber(candidate:match('nvim%-recovery%-(%d+)%-'))
      if ok and type(value) == 'table' and type(value.draft) == 'table' and type(value.text) == 'string'
        and (not owner or owner == uv.os_getpid() or not uv.kill(owner, 0)) then
        local claimed = recovery_path({})
        if uv.fs_rename(candidate, claimed) then
          recovered, path = value, claimed
          break
        end
      end
    end
    if recovered and not recovered.pending_apply and recovered.draft.draft_id == draft.draft_id
      and recovered.text == draft.text
      and draft.revision == recovered.draft.revision + (recovered.text == recovered.draft.text and 0 or 1) then
      recovered.draft = draft
    end
    local buf = api.nvim_create_buf(true, false)
    state = { buf = buf, draft = recovered and recovered.draft or draft,
      text = recovered and recovered.text or draft.text, recovery = path,
      pending_apply = recovered and recovered.pending_apply or nil }
    local s = state
    api.nvim_buf_set_name(buf, 'now://draft')
    vim.bo[buf].buftype = 'acwrite'
    vim.bo[buf].bufhidden = 'hide'
    vim.bo[buf].swapfile = false
    vim.bo[buf].filetype = 'now'
    vim.bo[buf].expandtab = true
    vim.bo[buf].shiftwidth = config.indent_size
    vim.bo[buf].softtabstop = config.indent_size
    vim.bo[buf].autoindent = false
    api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(s.text, '\n', { plain = true }))
    vim.bo[buf].modified = s.text ~= s.draft.text
    vim.b[buf].now_cwd = s.draft.cwd
    vim.b[buf].now_draft_path = root() .. '/draft.json'
    modifiable(s, not s.pending_apply)
    install_mappings(buf)
    api.nvim_create_autocmd({ 'TextChanged', 'TextChangedI' }, { buffer = buf, callback = function() changed(s) end })
    api.nvim_create_autocmd('BufWriteCmd', { buffer = buf, callback = M.save })
    api.nvim_create_autocmd('BufWinLeave', { buffer = buf, callback = function()
      if api.nvim_get_current_buf() == buf then s.cursor = api.nvim_win_get_cursor(0) end
      flush(s, false)
    end })
    api.nvim_create_autocmd({ 'BufUnload', 'BufDelete' }, { buffer = buf, callback = function() flush(s, true) end })
    api.nvim_create_autocmd('VimLeavePre', { once = true, callback = function() flush(s, true) end })
    vim.cmd.split()
    api.nvim_win_set_buf(0, buf)
    if not s.pending_apply then vim.cmd.startinsert() end
    if path then vim.notify("Now: recovered draft", vim.log.levels.INFO) end
  end)
end

function M.apply()
  local s = state
  if not s or not api.nvim_buf_is_valid(s.buf) or s.applying then return end
  s.applying = true
  modifiable(s, false)
  if s.pending_apply then
    if s.busy then s.applying = false; return end
    apply_saved(s)
  else
    pump(s)
  end
end

local function editing()
  return state and api.nvim_get_current_buf() == state.buf and vim.bo[state.buf].modifiable
end

local function line_parts(line)
  local space, body = line:match('^( *)(.*)$')
  local marker, title = body:match('^(%- %[[ xX]%] )(.*)$')
  if not marker then marker, title = body:match('^(%- )(.*)$') end
  return #space, marker or '', title or body
end

function M.submit()
  if not editing() then return end
  local row, col = unpack(api.nvim_win_get_cursor(0))
  local line = api.nvim_get_current_line()
  local depth, marker, title = line_parts(line)
  local prefix = string.rep(' ', depth)
  if vim.trim(title) == '' then
    if depth > 0 then
      local replacement = string.rep(' ', math.max(0, depth - config.indent_size))
      api.nvim_buf_set_lines(state.buf, row - 1, row, false, { replacement })
      api.nvim_win_set_cursor(0, { row, #replacement })
    else
      api.nvim_buf_set_lines(state.buf, row - 1, row, false, { '', '' })
      api.nvim_win_set_cursor(0, { row + 1, 0 })
    end
  else
    local split = math.max(0, col - depth - #marker)
    local before, after = title:sub(1, split), title:sub(split + 1)
    local first = prefix .. (marker ~= '' and marker or '- [ ] ') .. before
    local second = prefix .. after
    api.nvim_buf_set_lines(state.buf, row - 1, row, false, { first, second })
    api.nvim_win_set_cursor(0, { row + 1, #prefix })
  end
  changed(state)
end

local function subtree(lines, row, depth)
  local last = row
  for i = row + 1, #lines do
    if vim.trim(lines[i]) ~= '' and line_parts(lines[i]) <= depth then break end
    last = i
  end
  return last
end

local function shift(outdent)
  if not editing() then return end
  local lines = api.nvim_buf_get_lines(state.buf, 0, -1, false)
  local row, col = unpack(api.nvim_win_get_cursor(0))
  local depth = line_parts(lines[row])
  local size = config.indent_size
  local last = subtree(lines, row, depth)
  local parent, previous
  for i = row - 1, 1, -1 do
    if vim.trim(lines[i]) ~= '' then
      local d = line_parts(lines[i])
      if d == depth and not previous then previous = i end
      if d < depth then parent = i; break end
    end
  end
  if (outdent and (depth < size or not parent)) or (not outdent and not previous) then return end
  local moved = {}
  for i = row, last do
    moved[#moved + 1] = outdent and lines[i]:sub(math.min(size, (line_parts(lines[i]))) + 1)
      or (string.rep(' ', size) .. lines[i])
  end
  local target = row
  if outdent then
    local parent_last = subtree(lines, parent, line_parts(lines[parent]))
    local replacement = {}
    for i = last + 1, parent_last do replacement[#replacement + 1] = lines[i] end
    target = row + #replacement
    vim.list_extend(replacement, moved)
    api.nvim_buf_set_lines(state.buf, row - 1, parent_last, false, replacement)
  else
    api.nvim_buf_set_lines(state.buf, row - 1, last, false, moved)
  end
  api.nvim_win_set_cursor(0, { target, math.max(0, col + (outdent and -size or size)) })
  changed(state)
end

function M.indent() shift(false) end
function M.outdent() shift(true) end

function M.setup(opts)
  config = vim.tbl_deep_extend('force', vim.deepcopy(defaults), opts or {})
  assert(type(config.indent_size) == 'number' and config.indent_size > 0
    and config.indent_size % 1 == 0, 'now.indent_size must be a positive integer')
  assert(type(config.command) == 'table' and #config.command > 0, 'now.command must be an argv list')
  api.nvim_create_user_command('Now', M.open, { desc = 'Open durable Now draft', force = true })
  api.nvim_create_user_command('NowApply', M.apply, { desc = 'Apply the saved Now draft', force = true })
end

return M
