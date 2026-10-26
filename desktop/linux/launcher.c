/* A fixed native identity for the scoped AppArmor user-namespace permission. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char **child = calloc((size_t)argc + 5, sizeof(char *));
    if (child == NULL) return 1;
    child[0] = "/usr/bin/python3";
    child[1] = "-I";
    child[2] = "-B";
    child[3] = "/opt/foxlight/sovereign/sovereign-linux.py";
    for (int i = 1; i < argc; i++) child[i + 3] = argv[i];
    execv(child[0], child);
    perror("Sovereign could not open its native window");
    free(child);
    return 1;
}
