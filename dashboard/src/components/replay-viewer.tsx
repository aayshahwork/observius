"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Video } from "lucide-react";

interface ReplayViewerProps {
  replayUrl: string | null;
  loading?: boolean;
}

export function ReplayViewer({ replayUrl, loading = false }: ReplayViewerProps) {
  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Replay</CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <Skeleton className="aspect-video w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (!replayUrl) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Replay</CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="flex aspect-video items-center justify-center rounded-md bg-muted">
            <div className="text-center">
              <Video className="mx-auto size-8 text-muted-foreground" />
              <p className="mt-2 text-sm text-muted-foreground">
                No replay available
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Replay</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <iframe
          src={replayUrl}
          className="aspect-video w-full rounded-md border"
          title="Task replay"
          sandbox="allow-scripts allow-same-origin"
        />
      </CardContent>
    </Card>
  );
}
