using System;
using System.IO;

// Папку можно передать аргументом, иначе берётся текущая
string path = args.Length > 0 ? args[0] : Directory.GetCurrentDirectory();

if (!Directory.Exists(path))
{
    Console.WriteLine($"Папка не найдена: {path}");
    return;
}

Console.WriteLine($"Сортировка папки: {path}\n");
int moved = 0;

foreach (string file in Directory.GetFiles(path))
{
    string ext = Path.GetExtension(file).TrimStart('.').ToLowerInvariant();
    if (string.IsNullOrEmpty(ext)) ext = "без_расширения";

    string targetDir = Path.Combine(path, ext);
    Directory.CreateDirectory(targetDir);

    string fileName = Path.GetFileName(file);
    string dest = Path.Combine(targetDir, fileName);

    // Не перезаписываем существующие файлы — добавляем метку времени
    if (File.Exists(dest))
    {
        string name = Path.GetFileNameWithoutExtension(fileName);
        dest = Path.Combine(targetDir,
            $"{name}_{DateTime.Now:yyyyMMddHHmmss}{Path.GetExtension(file)}");
    }

    File.Move(file, dest);
    Console.WriteLine($"  {fileName}  ->  {ext}/");
    moved++;
}

Console.WriteLine($"\nГотово. Перемещено файлов: {moved}");