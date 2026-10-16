#!/data/data/com.termux/files/usr/bin/bash
# EON-Verso Shorts: abrir la interfaz web y sus registros de GitHub Actions.
URL="https://github.com/evansxandera1-tech/Eon-Verso/actions/workflows/eon_shorts_auto.yml"
if command -v termux-open-url >/dev/null 2>&1; then
  termux-open-url "$URL"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL"
else
  printf 'Abre este enlace en tu navegador:\n%s\n' "$URL"
fi
