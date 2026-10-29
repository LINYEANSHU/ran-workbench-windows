"""Packet and byte-range accounting; not an RLC protocol implementation."""
from collections import deque
from dataclasses import dataclass


@dataclass
class Packet:
    id: int
    size: int
    born: int
    cursor: int=0
    received: int=0
    delivered: bool=False
    dropped: bool=False


class Traffic:
    def __init__(self,policy,limit,dt):
        self.policy,self.limit,self.dt=policy,limit,dt
        self.queue=deque();self.credit=0.;self.next_id=0;self.buffer_bytes=0
        self.generated_packets=self.generated_bytes=self.delivered_packets=self.delivered_bytes=0
        self.dropped_packets=self.dropped_bytes=self.pending_packets=0
        self.latencies=[]

    def arrive(self,slot):
        size=self.policy['packet_bytes']
        if self.policy['mode']=='full_buffer':
            count=max(0,(min(self.limit,max(2000,size*4))-self.buffer_bytes)//size)
        else:
            self.credit+=self.policy['rate_mbps']*1e6/8*self.dt
            count=int(self.credit//size);self.credit-=count*size
        for _ in range(count):
            self.generated_packets+=1;self.generated_bytes+=size;self.next_id+=1
            if self.buffer_bytes+size>self.limit:
                self.dropped_packets+=1;self.dropped_bytes+=size
                continue
            self.queue.append(Packet(self.next_id,size,slot));self.buffer_bytes+=size;self.pending_packets+=1

    @property
    def has_data(self):
        while self.queue and (self.queue[0].dropped or self.queue[0].cursor==self.queue[0].size):self.queue.popleft()
        return bool(self.queue)

    def allocate(self,capacity):
        spans=[]
        while capacity>0 and self.has_data:
            p=self.queue[0];length=min(capacity,p.size-p.cursor)
            spans.append((p,p.cursor,length));p.cursor+=length;capacity-=length
            # New-data queue ownership ends at a BS-observed grant; the copy
            # retained for retransmission is in a separate, bounded HARQ buffer.
            self.buffer_bytes-=length
        return spans

    def receive(self,spans,slot):
        newly=0
        for p,_,length in spans:
            if p.dropped or p.delivered:continue
            p.received+=length
            if p.received==p.size:
                p.delivered=True;self.delivered_packets+=1;self.delivered_bytes+=p.size
                self.pending_packets-=1;newly+=p.size
                self.latencies.append(slot-p.born+1)
            elif p.received>p.size:raise RuntimeError('Duplicate packet byte delivery')
        return newly

    def drop(self,spans):
        for p,_,_ in spans:
            if p.delivered or p.dropped:continue
            p.dropped=True;self.dropped_packets+=1;self.dropped_bytes+=p.size
            self.buffer_bytes-=p.size-p.cursor;self.pending_packets-=1

    def summary(self):
        assert self.generated_packets==self.delivered_packets+self.dropped_packets+self.pending_packets
        return {k:getattr(self,k) for k in ('generated_packets','generated_bytes','delivered_packets','delivered_bytes','dropped_packets','dropped_bytes','pending_packets','buffer_bytes')}
