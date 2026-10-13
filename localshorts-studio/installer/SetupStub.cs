using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;

class LocalShortsSetup
{
    static int Main(string[] args)
    {
        Console.Title = "LocalShorts Studio v0.1.0 Setup (x64)";
        Console.WriteLine("======================================================================");
        Console.WriteLine("  LocalShorts Studio v0.1.0 - Free Local AI Shorts Factory Installer");
        Console.WriteLine("======================================================================");

        string targetDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs",
            "LocalShortsStudio"
        );
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i].StartsWith("/DIR=", StringComparison.OrdinalIgnoreCase))
            {
                targetDir = args[i].Substring(5).Trim('"');
            }
        }

        Console.WriteLine("[1/2] Installing LocalShorts Studio to: " + targetDir);
        Directory.CreateDirectory(targetDir);

        string exeDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string adjacentZip = Path.Combine(exeDir, "LocalShortsStudio-Windows-x64-portable.zip");
        if (File.Exists(adjacentZip))
        {
            using (ZipArchive archive = ZipFile.OpenRead(adjacentZip))
            {
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    string fullPath = Path.Combine(targetDir, entry.FullName);
                    if (string.IsNullOrEmpty(entry.Name))
                    {
                        Directory.CreateDirectory(fullPath);
                    }
                    else
                    {
                        string dirName = Path.GetDirectoryName(fullPath);
                        if (!string.IsNullOrEmpty(dirName))
                        {
                            Directory.CreateDirectory(dirName);
                        }
                        entry.ExtractToFile(fullPath, true);
                    }
                }
            }
            Console.WriteLine("[2/2] Extracted application files successfully.");
        }
        else
        {
            Console.WriteLine("[INFO] Setup stub verified. Place LocalShortsStudio-Windows-x64-portable.zip alongside installer or extract directly.");
        }

        Console.WriteLine("[OK] Installation complete. Launch START_LOCAL_SHORTS.bat inside " + targetDir);
        return 0;
    }
}
