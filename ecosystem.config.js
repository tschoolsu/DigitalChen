module.exports = {
  apps: [
    {
      name: "DigitalChen",
      script: "./bot.py",
      interpreter: "./.venv/bin/python",
      cwd: "/home/APP/DigitalChen",
      autorestart: true
    }
  ]
}
