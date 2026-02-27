run the following command:
./show-resources.ps1 | Tee-Object -FilePath show-resources-output.txt

Dependancies:
1. sql           (standalone, no dependencies)
2. container-registry  (standalone)
3. storage       (standalone)
4. key-vault     (standalone)
5. openai        (standalone)
6. log-analytics (needed by container-apps)
7. container-apps (depends on networking ✅ + log-analytics + acr)
8. front-door    (depends on container-apps)
9. static-web-app (depends on front-door)
10. grafana      (standalone)