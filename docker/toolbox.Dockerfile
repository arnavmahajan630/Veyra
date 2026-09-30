# The Windows fallback for ./veyra.sh: bash + Docker CLI + Compose + git, nothing else.
# veyra.ps1 runs it only when no WSL distro with Docker is available, mounting the Docker
# socket and the repo at Docker Desktop's host path (/run/desktop/mnt/host/<drive>/...), so
# the relative bind mounts compose sends to the daemon resolve on the Windows drive.
FROM docker:29.1-cli

RUN apk add --no-cache bash git coreutils findutils grep sed gawk procps ncurses

ENV VEYRA_TOOLBOX=1
ENTRYPOINT ["bash"]
