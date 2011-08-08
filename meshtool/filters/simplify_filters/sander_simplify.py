from meshtool.args import *
from meshtool.filters.base_filters import *
import inspect
import numpy
import networkx as nx
from itertools import izip, chain, repeat, imap
import datetime
import math
import __builtin__
import heapq
from render_utils import renderVerts, renderCharts
from graph_utils import astar_path, dfs_interior_nodes, super_cycle
import gc
import random

#python 2.5 set
if not 'set' in __builtin__.__dict__:
    import sets
    set = sets.Set

#after python2.5, uniqu1d was renamed to unique
args, varargs, keywords, defaults = inspect.getargspec(numpy.unique)
if 'return_inverse' not in args:
    numpy.unique = numpy.unique1d

def timer():
    begintime = datetime.datetime.now()
    while True:
        curtime = datetime.datetime.now()
        yield (curtime-begintime)
        begintime = curtime
         
def calcPerimeter(pts):
    dx = pts[:,0,0]-pts[:,1,0]
    dy = pts[:,0,1]-pts[:,1,1]
    dz = pts[:,0,2]-pts[:,1,2]
    return numpy.sum(numpy.sqrt(dx*dx + dy*dy + dz*dz))

def v3dist(pt1, pt2):
    d = pt1 - pt2
    return math.sqrt(d[0]*d[0] + d[1]*d[1] + d[2]*d[2])

def array_mult(arr1, arr2):
    return arr1[:,0]*arr2[:,0] + arr1[:,1]*arr2[:,1] + arr2[:,2]*arr1[:,2]
def array_dot(arr1, arr2):
    return numpy.sqrt( array_mult(arr1, arr2) )

def calcFitError(pts):
    # this computes the outer product of each vector
    # in the array, element-wise, then sums up the 3x3
    # matrices
    #  A = sum{v_i * v_i^T} 
    A = numpy.sum(pts[...,None] * pts[:,None,:], 0)
    
    # b = mean(v_i)
    b = numpy.mean(pts, 0)

    # Z = A - (b * b^T) / c
    Z = A - numpy.outer(b,b) / len(pts)
    
    # n (normal of best fit plane) is the eigenvector
    # corresponding to the minimum eigenvalue
    eigvals, eigvecs = numpy.linalg.eig(Z)
    n = eigvecs[numpy.argmin(eigvals)]
    
    # d (scalar offset of best fit plane) = -n^T * b / c
    d = numpy.inner(-n, b) / len(pts)
    
    # final error is the square of the mean distance of each point to the plane
    mean_dist = numpy.mean(array_mult(n[None,:].repeat(len(pts), axis=0), pts) + d)
    Efit = mean_dist * mean_dist
    
    return Efit


def tri_areas_3d(arr):
    crosses = numpy.cross(arr[:,0] - arr[:,1], arr[:,0] - arr[:,2])
    return array_dot(crosses, crosses) / 2.0
def tri_areas_2d(arr):
    return ( arr[:,0,0]*(arr[:,1,1]-arr[:,2,1]) + 
             arr[:,1,0]*(arr[:,2,1]-arr[:,0,1]) + 
             arr[:,2,0]*(arr[:,0,1]-arr[:,1,1])
            ) / 2.0

def stretch_metric(t3d, t2d, return_A2d=False, flippedCheck=None):
    q1 = t3d[:,0]
    q2 = t3d[:,1]
    q3 = t3d[:,2]
    s1 = t2d[:,0,0]
    s2 = t2d[:,1,0]
    s3 = t2d[:,2,0]
    t1 = t2d[:,0,1]
    t2 = t2d[:,1,1]
    t3 = t2d[:,2,1]
    A2d = ((s2-s1)*(t3-t1) - (s3-s1)*(t2-t1)) / 2.0
    
    S_s = (q1*(t2-t3)[:,None] + q2*(t3-t1)[:,None] + q3*(t1-t2)[:,None]) / (2.0 * A2d)[:,None]
    S_t = (q1*(s3-s2)[:,None] + q2*(s1-s3)[:,None] + q3*(s2-s1)[:,None]) / (2.0 * A2d)[:,None]
    
    L2 = numpy.sqrt((array_mult(S_s,S_s) + array_mult(S_t,S_t)) / 2.0)
    if flippedCheck is not None:
        L2[numpy.logical_not(numpy.logical_xor(A2d < 0, flippedCheck < 0))] = numpy.inf
    #L2[A2d < 0] = numpy.Inf
    #if normalize:
    #    A3d = tri_areas_3d(t3d)
    #    L2 = numpy.sqrt(numpy.sum(L2*L2*A3d) / numpy.sum(A3d)) * numpy.sqrt(numpy.sum(A2d) / numpy.sum(A3d))
    if return_A2d:
        return L2, A2d
    return L2

def begin_operation():
    gc.disable()
def end_operation():
    gc.enable()

def sandler_simplify(mesh):
    all_vertices = []
    all_indices = []
    vertex_offset = 0
    t = timer()
    
    print 'building aggregated vertex and triangle list...',
    begin_operation()
    for boundgeom in mesh.scene.objects('geometry'):
        for boundprim in boundgeom.primitives():
            all_vertices.append(boundprim.vertex)
            all_indices.append(boundprim.vertex_index + vertex_offset)
            vertex_offset += len(boundprim.vertex)
    for boundcontroller in mesh.scene.objects('controller'):
        boundgeom = boundcontroller.geometry
        for boundprim in boundgeom.primitives():
            all_vertices.append(boundprim.vertex)
            all_indices.append(boundprim.vertex_index + vertex_offset)
            vertex_offset += len(boundprim.vertex)
            
    all_vertices = numpy.concatenate(all_vertices)
    all_indices = numpy.concatenate(all_indices)
    end_operation()
    print next(t)
    
    print 'uniqifying the list...',
    begin_operation()
    unique_data, index_map = numpy.unique(all_vertices.view([('',all_vertices.dtype)]*all_vertices.shape[1]), return_inverse=True)
    all_vertices = unique_data.view(all_vertices.dtype).reshape(-1,all_vertices.shape[1])
    all_indices = index_map[all_indices]
    
    #scale to known range so error values are normalized
    all_vertices[:,0] -= numpy.min(all_vertices[:,0])
    all_vertices[:,1] -= numpy.min(all_vertices[:,1])
    all_vertices[:,2] -= numpy.min(all_vertices[:,2])
    all_vertices *= 1000.0 / numpy.max(all_vertices)
    
    end_operation()
    print next(t)
    
    print 'building vertex vertices...',
    begin_operation()
    vertexgraph = nx.Graph()
    vertexgraph.add_nodes_from(xrange(len(all_vertices)))
    end_operation()
    print next(t)
    
    print 'building vertex edges...',
    begin_operation()
    vertexgraph.add_edges_from(all_indices[:,(0,1)])
    vertexgraph.add_edges_from(all_indices[:,(0,2)])
    vertexgraph.add_edges_from(all_indices[:,(1,2)])
    
    vertexgraph.add_nodes_from(( (edge[0], {facenum: True})
                                 for facenum, edge in
                                 enumerate(all_indices[:,(0,1)]) ))
    vertexgraph.add_nodes_from(( (edge[0], {facenum: True})
                                 for facenum, edge in
                                 enumerate(all_indices[:,(0,2)]) ))
    vertexgraph.add_nodes_from(( (edge[0], {facenum: True})
                                 for facenum, edge in
                                 enumerate(all_indices[:,(1,2)]) ))
    vertexgraph.add_nodes_from(( (edge[1], {facenum: True})
                                 for facenum, edge in
                                 enumerate(all_indices[:,(0,1)]) ))
    vertexgraph.add_nodes_from(( (edge[1], {facenum: True})
                                 for facenum, edge in
                                 enumerate(all_indices[:,(0,2)]) ))
    vertexgraph.add_nodes_from(( (edge[1], {facenum: True})
                                 for facenum, edge in
                                 enumerate(all_indices[:,(1,2)]) ))
    end_operation()
    print next(t)
    
    print 'number of faces', len(all_indices)
    print 'number of connected components in vertex graph =', nx.algorithms.components.connected.number_connected_components(vertexgraph)
    
    print 'building face vertices...',
    begin_operation()
    facegraph = nx.Graph()
    facegraph.add_nodes_from(( (i, {'tris':[tri], 
                                    'edges':set([tuple(sorted([tri[0], tri[1]])),
                                                 tuple(sorted([tri[1], tri[2]])),
                                                 tuple(sorted([tri[0], tri[2]]))])})
                               for i, tri in
                               enumerate(all_indices) ))
    end_operation()
    print next(t)
    
    print 'building face edges...',
    begin_operation()
    for (v1, v2) in vertexgraph.edges_iter():
        adj_v1 = set(vertexgraph.node[v1].keys())
        adj_v2 = set(vertexgraph.node[v2].keys())
        adj_both = adj_v1.intersection(adj_v2)
        if len(adj_both) == 2:
            facegraph.add_edge(*adj_both)
    end_operation()
    print next(t)
    
    merge_priorities = []
    maxerror = 0
    
    print 'calculating error...',
    begin_operation()
    for v1, v2 in facegraph.edges_iter():
        edges1 = facegraph.node[v1]['edges']
        edges2 = facegraph.node[v2]['edges']
        merged = numpy.array(list(edges1.symmetric_difference(edges2)))
        if len(merged) > 0:
            error = calcPerimeter(all_vertices[merged])**2
            error += calcFitError(all_vertices[merged.flatten()])
            if error > maxerror: maxerror = error
            merge_priorities.append((error, (v1, v2)))
    end_operation()
    print next(t)
        
    print 'creating priority queue...',
    begin_operation()
    heapq.heapify(merge_priorities)
    end_operation()
    print next(t)
    
    print 'merging charts...',
    begin_operation()
    node_count = len(all_indices)
    while len(merge_priorities) > 0:
        (error, (face1, face2)) = heapq.heappop(merge_priorities)
        
        #this can happen if we have already merged one of these
        if face1 not in facegraph or face2 not in facegraph:
            continue
        
        edges1 = facegraph.node[face1]['edges']
        edges2 = facegraph.node[face2]['edges']
        combined_edges = edges1.symmetric_difference(edges2)

        #check if boundary if more than one connected component
        connected_components_graph = nx.from_edgelist(combined_edges)
        if nx.algorithms.components.connected.number_connected_components(connected_components_graph) > 1:           
            continue

        # if the number of corners of the merged face is less than 3, disqualify it
        # where a "corner" is defined as a vertex with at least 3 adjacent faces
        combined_vertices = set(chain.from_iterable(combined_edges))
        newcorners = set()
        for v in combined_vertices:
            adj_v = set(vertexgraph.node[v].keys())
            if face1 in adj_v or face2 in adj_v:
                adj_v.add(-1) #standin for new face
            adj_v.discard(face1)
            adj_v.discard(face2)
            if len(adj_v) >= 3:
                newcorners.add(v)
        if len(newcorners) < 1:
            continue
        
        logrel = math.log(1 + error) / math.log(1 + maxerror)
        if logrel > 0.9:
            break
        #print 'error', error, 'maxerror', maxerror, 'logrel', logrel, 'merged left', len(merge_priorities), 'numfaces', len(facegraph)
        
        newface = node_count
        node_count += 1
        
        edges_to_add = []
        
        adj_faces = set(facegraph.neighbors(face1))
        adj_faces = adj_faces.union(set(facegraph.neighbors(face2)))
        adj_faces.remove(face1)
        adj_faces.remove(face2)
        
        invalidmerge = False
        for otherface in adj_faces:
            otheredges = facegraph.node[otherface]['edges']
            commonedges = combined_edges.intersection(otheredges)
            
            connected_components_graph = nx.from_edgelist(commonedges)
            
            #invalid merge if border between merged face and neighbor is more than one connected component
            if nx.algorithms.components.connected.number_connected_components(connected_components_graph) > 1:
                invalidmerge = True
                break
            #or more than one cycle
            cycles = nx.algorithms.cycles.cycle_basis(connected_components_graph)
            if len(cycles) > 1:
                invalidmerge = True
                break

            vertices = set(chain.from_iterable(otheredges))
            othernewcorners = set()
            for v in vertices:
                adj_v = set(vertexgraph.node[v].keys())
                if face1 in adj_v or face2 in adj_v:
                    adj_v.add(newface)
                adj_v.discard(face1)
                adj_v.discard(face2)
                if len(adj_v) >= 3:
                    othernewcorners.add(v)
            
            #invalid merge if neighbor would have less than 3 corners
            if len(othernewcorners) < 1:
                invalidmerge = True
                break
            
            edges_to_add.append((newface, otherface))

        if invalidmerge:
            continue

        combined_tris = facegraph.node[face1]['tris'] + facegraph.node[face2]['tris']
        facegraph.add_node(newface, tris=combined_tris, edges=combined_edges)        
        facegraph.add_edges_from(edges_to_add)
        for otherface in adj_faces:
            otheredges = facegraph.node[otherface]['edges']
            merged = numpy.array(list(combined_edges.symmetric_difference(otheredges)))
            if len(merged) > 0:
                error = calcPerimeter(all_vertices[merged])**2
                error += calcFitError(all_vertices[merged.flatten()])
                if error > maxerror: maxerror = error
                heapq.heappush(merge_priorities, (error, (newface, otherface)))

        for v in combined_vertices:
            if face1 in vertexgraph.node[v]:
                del vertexgraph.node[v][face1]
            if face2 in vertexgraph.node[v]:
                del vertexgraph.node[v][face2]
            vertexgraph.node[v][newface] = True

        facegraph.remove_node(face1)
        facegraph.remove_node(face2)

    end_operation()
    print next(t)
    
    print 'final number of faces =', len(facegraph)
    print 'final number of connected components =', nx.algorithms.components.connected.number_connected_components(facegraph)
    
    print 'updating corners...',
    begin_operation()
    for face, facedata in facegraph.nodes_iter(data=True):
        edges = facegraph.node[face]['edges']
        vertices = set(chain.from_iterable(edges))
        corners = set((v for v in vertices if len(vertexgraph.node[v]) >= 3))
        facegraph.node[face]['corners'] = corners
    end_operation()
    print next(t)
    
    print 'computing distance between points',
    begin_operation()
    for v1, v2 in vertexgraph.edges_iter():
        vertexgraph.add_edge(v1, v2, distance=v3dist(all_vertices[v1],all_vertices[v2]))
    end_operation()
    print next(t)

    print 'straightening chart boundaries...',
    begin_operation()
    for (face1, face2) in facegraph.edges_iter():
        
        #can't straighten the border of a single triangle
        tris1 = facegraph.node[face1]['tris']
        tris2 = facegraph.node[face2]['tris']
        if len(tris1) <= 1 or len(tris2) <= 1:
            continue
        
        edges1 = facegraph.node[face1]['edges']
        edges2 = facegraph.node[face2]['edges']
        combined_edges = edges1.symmetric_difference(edges2)
        shared_edges = edges1.intersection(edges2)
        
        #dont bother trying to straighten a single edge
        if len(shared_edges) == 1:
            continue
        
        shared_vertices = set(chain.from_iterable(shared_edges))
        corners1 = facegraph.node[face1]['corners']
        corners2 = facegraph.node[face2]['corners']
        combined_corners = corners1.intersection(corners2).intersection(shared_vertices)
        
        if len(combined_corners) < 1 or len(combined_corners) > 2:
            print 'warn combined corners wrong'
            continue
        
        giveup = False
        if len(combined_corners) == 2:
            start_path, end_path = combined_corners
        elif len(combined_corners) == 1:
            pt2edge = {}
            for src, dest in shared_edges:
                srclist = pt2edge.get(src, [])
                srclist.append((src, dest))
                pt2edge[src] = srclist
                dstlist = pt2edge.get(dest, [])
                dstlist.append((src, dest))
                pt2edge[dest] = dstlist
            start_path = combined_corners.pop()
            curpt = start_path
            shared_path = []
            while curpt in pt2edge:
                edge = pt2edge[curpt][0]
                sanedge = edge
                if edge[0] != curpt:
                    sanedge = (edge[1], edge[0])
                nextpt = sanedge[1]
                shared_path.append(sanedge)
                nextopts = pt2edge[nextpt]
                if edge not in nextopts:
                    print 'warn corner1 bad'
                    giveup = True
                    break
                nextopts.remove(edge)
                if len(nextopts) > 0:
                    pt2edge[nextpt] = nextopts
                else:
                    del pt2edge[nextpt]
                curpt = nextpt
            
            end_path = shared_path[-1][1] 
        if giveup:
            continue
        
        edges1 = edges1.symmetric_difference(shared_edges)
        edges2 = edges2.symmetric_difference(shared_edges)
        stop_nodes = set(chain.from_iterable(combined_edges))
        combined_tris = tris1 + tris2
        constrained_set = set(chain.from_iterable(combined_tris))
        
        try:
            straightened_path = astar_path(vertexgraph, start_path, end_path,
                                           heuristic=lambda x,y: v3dist(all_vertices[x], all_vertices[y]),
                                           weight='distance', subset=constrained_set, exclude=stop_nodes)
        except nx.exception.NetworkXNoPath:
            continue
        
        # if we already have the shortest path, nothing to do
        if set(shared_vertices) == set(straightened_path):
            continue
        
        new_combined_edges = []
        for i in range(len(straightened_path)-1):
            new_combined_edges.append(tuple(sorted((straightened_path[i], straightened_path[i+1]))))
        new_combined_edges = set(new_combined_edges)
        new_edges1 = edges1.symmetric_difference(new_combined_edges)
        new_edges2 = edges2.symmetric_difference(new_combined_edges)
        
        # This can happen if the shortest path actually encompasses
        # the smaller face, but this would be equivalent to merging the
        # two faces. If we didn't merge these two in the previous step,
        # it was because the cost was too high or it would violate one of
        # the constraints, so just ignore this 
        if len(new_edges1) == 0 or len(new_edges2) == 0:
            continue
        
        boundary1 = set(chain.from_iterable(new_edges1))
        boundary2 = set(chain.from_iterable(new_edges2))
        boundary = boundary1.union(boundary2).union(straightened_path)
        
        vertexset1 = boundary1.difference(straightened_path)
        vertexset2 = boundary2.difference(straightened_path)
        
        allin1 = list(dfs_interior_nodes(vertexgraph,
                                         starting=vertexset1,
                                         boundary=boundary,
                                         subset=constrained_set.difference(boundary2)))
        allin2 = list(dfs_interior_nodes(vertexgraph,
                                         starting=vertexset2,
                                         boundary=boundary,
                                         subset=constrained_set.difference(boundary1)))
        
        vertexset1 = set(allin1).union(vertexset1).union(straightened_path)
        vertexset2 = set(allin2).union(vertexset2).union(straightened_path)
        tris1 = []
        tris2 = []
        trisneither = []
        for tri in combined_tris:
            if tri[0] in vertexset1 and tri[1] in vertexset1 and tri[2] in vertexset1:
                tris1.append(tri)
            elif tri[0] in vertexset2 and tri[1] in vertexset2 and tri[2] in vertexset2:
                tris2.append(tri)
            else:
                trisneither.append(tri)
        
        #this can happen if the straightened path cuts off another face's edges
        if len(trisneither) != 0:
            print 'warn trisneither'
            continue
        
        # This can happen if the shortest path actually encompasses
        # the smaller face, but this would be equivalent to merging the
        # two faces. If we didn't merge these two in the previous step,
        # it was because the cost was too high or it would violate one of
        # the constraints, so just ignore this 
        if len(tris1) == 0 or len(tris2) == 0:
            print 'warn encompassing2'
            continue
        
        #this can happen if the straightened path cuts off another face's edges
        if len(tris1) + len(tris2) != len(combined_tris):
            print 'warn cutoff'
            continue

        facegraph.add_edge(face1, face2)
        facegraph.add_node(face1, tris=tris1, edges=new_edges1)
        facegraph.add_node(face2, tris=tris2, edges=new_edges2)
        
    end_operation()
    print next(t)
    
    print 'forming initial chart parameterizations...',
    begin_operation()
    for (face, facedata) in facegraph.nodes_iter(data=True):
        border_edges = facedata['edges']
        chart_tris = facedata['tris']
        
        unique_verts = numpy.unique(numpy.array(chart_tris))
        border_verts = numpy.unique(numpy.array(list(border_edges)))
        interior_verts = numpy.setdiff1d(unique_verts, border_verts, assume_unique=True)
                
        bordergraph = nx.from_edgelist(border_edges)
        bigcycle = list(super_cycle(bordergraph))
        boundary_path = []
        for i in range(len(bigcycle)-1):
            boundary_path.append((bigcycle[i], bigcycle[i+1]))
        boundary_path.append((bigcycle[len(bigcycle)-1], bigcycle[0]))
        assert(len(boundary_path) == len(border_edges))

        total_dist = 0
        for (v1, v2) in boundary_path:
            total_dist += v3dist(all_vertices[v1], all_vertices[v2])
        
        curangle = 0
        for edge in boundary_path:
            angle = v3dist(all_vertices[edge[0]], all_vertices[edge[1]]) / total_dist
            curangle += angle * 2 * math.pi
            x, y = (math.sin(curangle) + 1) / 2.0, (math.cos(curangle) + 1.0) / 2.0
            vertexgraph.add_node(edge[0], u=x)
            vertexgraph.add_node(edge[0], v=y)
        
        if len(interior_verts) > 0:
        
            vert2idx = {}
            for i, v in enumerate(interior_verts):
                vert2idx[v] = i
            
            A = numpy.zeros(shape=(len(interior_verts), len(interior_verts)), dtype=numpy.float32)
            Bu = numpy.zeros(len(interior_verts))
            Bv = numpy.zeros(len(interior_verts))
            sumu = numpy.zeros(len(interior_verts))
            
            for edge in vertexgraph.subgraph(unique_verts).edges_iter():
                v1, v2 = edge
                if v1 in border_verts and v2 in border_verts:
                    continue
                
                edgelen = v3dist(all_vertices[v1], all_vertices[v2])
                if v1 in border_verts:
                    Bu[vert2idx[v2]] += edgelen * vertexgraph.node[v1]['u']
                    Bv[vert2idx[v2]] += edgelen * vertexgraph.node[v1]['v']
                    sumu[vert2idx[v2]] += edgelen
                elif v2 in border_verts:
                    Bu[vert2idx[v1]] += edgelen * vertexgraph.node[v2]['u']
                    Bv[vert2idx[v1]] += edgelen * vertexgraph.node[v2]['v']
                    sumu[vert2idx[v1]] += edgelen
                else:
                    A[vert2idx[v1]][vert2idx[v2]] = -1 * edgelen
                    A[vert2idx[v2]][vert2idx[v1]] = -1 * edgelen
                    sumu[vert2idx[v1]] += edgelen
                    sumu[vert2idx[v2]] += edgelen
            
            Bu.shape = (len(Bu), 1)
            Bv.shape = (len(Bv), 1)
            sumu.shape = (len(sumu), 1)
            
            A /= sumu
            Bu /= sumu
            Bv /= sumu
            numpy.fill_diagonal(A, 1)
            
            interior_us = numpy.linalg.solve(A, Bu)
            interior_vs = numpy.linalg.solve(A, Bv)
            for (i, (u, v)) in enumerate(zip(interior_us, interior_vs)):
                vertexgraph.add_node(interior_verts[i], u=u[0], v=v[0], stretch=0)
        
        #import Image, ImageDraw
        #W, H = 500, 500
        #im = Image.new("RGB", (W,H), (255,255,255))
        #draw = ImageDraw.Draw(im)
        
        #for edge in vertexgraph.subgraph(unique_verts).edges_iter():
        #    pt1, pt2 = edge
        #    u1 = vertexgraph.node[pt1]['u']
        #    u2 = vertexgraph.node[pt2]['u']
        #    v1 = vertexgraph.node[pt1]['v']
        #    v2 = vertexgraph.node[pt2]['v']
        #    uv1 = ( u1 * W, v1 * H )
        #    uv2 = ( u2 * W, v2 * H )
        #    draw.ellipse((uv1[0]-5, uv1[1]-5, uv1[0]+5, uv1[1]+5), outline=(0,0,0), fill=(255,0,0))
        #    draw.ellipse((uv2[0]-5, uv2[1]-5, uv2[0]+5, uv2[1]+5), outline=(0,0,0), fill=(255,0,0))
        #    draw.line([uv1, uv2], fill=(0,0,0))
        
        #del draw
        #im.show()
        #blah = raw_input()
        
        #import sys
        #sys.exit(0)
        
    end_operation()
    print next(t)
    
    print 'optimizing chart parameterizations...',
    begin_operation()
    for (face, facedata) in facegraph.nodes_iter(data=True):
        border_edges = facedata['edges']
        chart_tris = numpy.array(facedata['tris'])
        tri_3d = all_vertices[chart_tris]
        tri_2d = numpy.array([[
                   [vertexgraph.node[tri[0]]['u'], vertexgraph.node[tri[0]]['v']],
                   [vertexgraph.node[tri[1]]['u'], vertexgraph.node[tri[1]]['v']],
                   [vertexgraph.node[tri[2]]['u'], vertexgraph.node[tri[2]]['v']]]
                  for tri in chart_tris ])
        
        #TODO: REMOVEME
        if len(chart_tris) < 2:
            continue
        
        L2 = stretch_metric(tri_3d, tri_2d)
        
        unique_verts, index_map = numpy.unique(chart_tris, return_inverse=True)
        index_map.shape = chart_tris.shape
        neighborhood_stretch = numpy.zeros(unique_verts.shape, dtype=numpy.float32)

        neighborhood_stretch[index_map[:,0]] += L2
        neighborhood_stretch[index_map[:,1]] += L2
        neighborhood_stretch[index_map[:,2]] += L2

        vert_stretch_heap = zip(-1 * neighborhood_stretch, unique_verts)
        heapq.heapify(vert_stretch_heap)
        
        while len(vert_stretch_heap) > 0:
            stretch, vert = heapq.heappop(vert_stretch_heap)
            print vert, stretch
            
            ucoord, vcoord = vertexgraph.node[vert]['u'], vertexgraph.node[vert]['v']
            
            adj_tris = chart_tris[numpy.logical_or(numpy.logical_or(
                            chart_tris[:,0] == vert,
                            chart_tris[:,1] == vert),
                            chart_tris[:,2] == vert)]
            neighborhood_tri3d = all_vertices[adj_tris]
            neighborhood_tri_2d = numpy.array([[
                       [vertexgraph.node[tri[0]]['u'], vertexgraph.node[tri[0]]['v']],
                       [vertexgraph.node[tri[1]]['u'], vertexgraph.node[tri[1]]['v']],
                       [vertexgraph.node[tri[2]]['u'], vertexgraph.node[tri[2]]['v']]]
                      for tri in adj_tris ])
            neighborhood_L2, origA2d = stretch_metric(neighborhood_tri3d, neighborhood_tri_2d, return_A2d=True)
            print 'orig L2', neighborhood_L2
            
            randangle = random.uniform(0, 2 * math.pi)
            randslope = math.tan(randangle)

            # y - y1 = m(x - x1)
            def yfromx(x):
                return randslope * (x - ucoord) + vcoord
            def xfromy(y):
                return ((y - vcoord) / randslope) + ucoord
            
            xintercept0 = yfromx(0)
            minx = 0 if 0 < xintercept0 < 1 else xfromy(1)
            xintercept1 = yfromx(1)
            maxx = 1 if 0 < xintercept1 < 1 else xfromy(0)
            minx, maxx = tuple(sorted([minx, maxx]))
            
            samples = 10.0
            step = (maxx - minx) / samples
            for xval in numpy.arange(minx, maxx, step):
                yval = yfromx(xval)
                print 'x,y', xval, yval
                vertexgraph.add_node(vert, u=xval, v=yval)
                neighborhood_tri_2d = numpy.array([[
                           [vertexgraph.node[tri[0]]['u'], vertexgraph.node[tri[0]]['v']],
                           [vertexgraph.node[tri[1]]['u'], vertexgraph.node[tri[1]]['v']],
                           [vertexgraph.node[tri[2]]['u'], vertexgraph.node[tri[2]]['v']]]
                          for tri in adj_tris ])
                neighborhood_L2 = stretch_metric(neighborhood_tri3d, neighborhood_tri_2d, flippedCheck=origA2d)
                print 'trying L2', neighborhood_L2
            
            import sys
            sys.exit(0)
        
    end_operation()
    print next(t)
    
    return mesh

def FilterGenerator():
    class SandlerSimplificationFilter(OpFilter):
        def __init__(self):
            super(SandlerSimplificationFilter, self).__init__('sander_simplify', 'Simplifies the mesh based on sandler, et al. method.')
        def apply(self, mesh):
            sandler_simplify(mesh)
            return mesh
    return SandlerSimplificationFilter()
from meshtool.filters import factory
factory.register(FilterGenerator().name, FilterGenerator)