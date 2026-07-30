#include <errno.h>
#include <stdio.h>
#include <unistd.h>

int main(int argc, char **argv) {
  char *arguments[6];

  if (argc != 2) {
    fputs("usage: xml-format PATH\n", stderr);
    return 2;
  }

  arguments[0] = "/opt/libxml2/bin/xmllint";
  arguments[1] = "--format";
  arguments[2] = "--output";
  arguments[3] = argv[1];
  arguments[4] = argv[1];
  arguments[5] = NULL;
  execv(arguments[0], arguments);
  perror("xmllint");
  return errno;
}
