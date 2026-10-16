using System;
using System.Text;

namespace STS2Platform.NativeFoundation;

// The one public-text projection for native rich text, shared by Reference (Connector)
// and Managed. BBCode style tags are removed; an inline icon [img]res://.../name.png[/img]
// becomes [name.png]. Anything unresolved (e.g. a {template}) is left visible so callers
// can reject it instead of publishing misleading text.
public static class NativeVisibleText
{
    public static string Strip(string text)
    {
        var sb = new StringBuilder();
        int i = 0;
        while (i < text.Length)
        {
            if (text[i] == '[')
            {
                if (text.AsSpan(i).StartsWith("[img]"))
                {
                    int contentStart = i + 5;
                    int closeTag = text.IndexOf("[/img]", contentStart, StringComparison.Ordinal);
                    if (closeTag >= 0)
                    {
                        string path = text[contentStart..closeTag];
                        int lastSlash = path.LastIndexOf('/');
                        sb.Append('[').Append(lastSlash >= 0 ? path[(lastSlash + 1)..] : path).Append(']');
                        i = closeTag + 6;
                        continue;
                    }
                }
                int end = text.IndexOf(']', i);
                if (end >= 0) { i = end + 1; continue; }
            }
            sb.Append(text[i]);
            i++;
        }
        return sb.ToString();
    }
}
