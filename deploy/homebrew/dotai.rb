# DotAI Homebrew formula.
#
# To publish: create a repo named `homebrew-dotai` under your GitHub account,
# put this file at Formula/dotai.rb — then anyone can:
#
#   brew tap wibawasuyadnya/dotai
#   brew install --HEAD dotai
#
class Dotai < Formula
  desc "Local multi-agent AI for your terminal — Claude, Codex, OpenRouter, llama.cpp"
  homepage "https://wibawasuyadnya.github.io/dotai"
  head "https://github.com/wibawasuyadnya/dotai.git", branch: "main"
  license "MIT"

  depends_on "python@3.12"

  def install
    libexec.install Dir["*"], ".env.example"
    (bin/"dotai").write <<~EOS
      #!/bin/bash
      DIR="$HOME/.config/local-ai"
      if [ ! -f "$DIR/ai-agent.py" ]; then
        bash "#{libexec}/install.sh" --local "#{libexec}"
      fi
      exec python3 "$DIR/ai-agent.py" --talk "$@"
    EOS
  end

  def caveats
    <<~EOS
      First run bootstraps ~/.config/local-ai with YOUR own config:
        dotai                # or add `source ~/.config/local-ai/ai-hook.sh`
                             # to your shell rc for the full `ai` command
      Add your keys to ~/.config/local-ai/.env (OpenRouter), or install the
      claude / codex CLIs, or use neither — the local Hermes model
      auto-downloads on first use (~1 GB).
    EOS
  end

  test do
    assert_predicate libexec/"ai-agent.py", :exist?
  end
end
