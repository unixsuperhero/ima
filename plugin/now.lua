if vim.g.loaded_now then return end
vim.g.loaded_now = true
vim.api.nvim_create_user_command('Now', function() require('now').open() end, { desc = 'Open durable Now draft' })
vim.api.nvim_create_user_command('NowApply', function() require('now').apply() end, { desc = 'Apply the saved Now draft' })
