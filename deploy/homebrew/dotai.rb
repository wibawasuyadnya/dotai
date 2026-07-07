# DotAI Homebrew formula.
#
# Published in the tap repo github.com/wibawasuyadnya/homebrew-dotai
# (as Formula/dotai.rb — keep both copies in sync). Install:
#
#   brew install wibawasuyadnya/dotai/dotai      # one-liner (auto-taps)
#
# New release checklist: git tag vX.Y.Z && git push origin vX.Y.Z, then
# update `url`/`sha256` here (shasum -a 256 <tag tarball>) and in the tap.
class Dotai < Formula
  desc "Local multi-agent AI for your terminal — Claude, Codex, OpenRouter, llama.cpp"
  homepage "https://wibawasuyadnya.github.io/dotai"
  url "https://github.com/wibawasuyadnya/dotai/archive/refs/tags/v0.9.0.tar.gz"
  sha256 "32e5a4c557c87003481a52297cc25e92fb93e192e7bffb00bef70aca0f59aae1"
  license "MIT"
  head "https://github.com/wibawasuyadnya/dotai.git", branch: "main"

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
