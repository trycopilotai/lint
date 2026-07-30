#include <errno.h>
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>

int run_formatter(char *path) {
  char *arguments[6];
  pid_t child;
  int status;

  arguments[0] = "/opt/ktlint-java/bin/java";
  arguments[1] = "-jar";
  arguments[2] = "/ktlint.jar";
  arguments[3] = "--format";
  arguments[4] = path;
  arguments[5] = NULL;

  child = fork();
  if (child < 0) {
    perror("fork");
    return errno;
  }
  if (child == 0) {
    execv(arguments[0], arguments);
    perror("java");
    _exit(errno);
  }
  if (waitpid(child, &status, 0) < 0) {
    perror("waitpid");
    return errno;
  }
  if (!WIFEXITED(status)) {
    return 1;
  }
  return WEXITSTATUS(status);
}

int main(int argc, char **argv) {
  int result;

  if (argc != 2) {
    fputs("usage: kotlin-format PATH\n", stderr);
    return 2;
  }
  result = run_formatter(argv[1]);
  if (result != 0) {
    return result;
  }
  return run_formatter(argv[1]);
}
